"""Planted stories, verified with independent DuckDB SQL over the raw files."""

from __future__ import annotations

import pytest


def rev(raw, start: str, end: str, where: str = "TRUE") -> float:
    return raw.execute(
        f"SELECT sum(net_amount) FROM sales WHERE order_date BETWEEN ?::DATE AND ?::DATE AND ({where})",
        [start, end],
    ).fetchone()[0]


JUL = ("2026-07-01", "2026-07-31")
AUG = ("2026-08-01", "2026-08-31")


def test_august_revenue_down_11_8_pct(raw, scenarios):
    j, a = rev(raw, *JUL), rev(raw, *AUG)
    pct = a / j - 1
    assert pct == pytest.approx(-0.118, abs=0.003)
    story = scenarios["stories"]["revenue_decline_aug_2026"]
    assert story["headline"]["pct_change"] == pytest.approx(pct, abs=1e-5)
    assert story["headline"]["current"] == pytest.approx(a, abs=0.01)


def test_cancelled_orders_are_excluded(raw):
    incl = raw.execute(
        "SELECT sum(net_amount) FROM order_lines JOIN orders USING (order_id) "
        "WHERE order_date BETWEEN '2026-08-01' AND '2026-08-31'"
    ).fetchone()[0]
    assert incl > rev(raw, *AUG) * 1.01


def test_dallas_explains_about_half(raw):
    total = rev(raw, *AUG) - rev(raw, *JUL)
    dallas = rev(raw, *AUG, "branch_name = 'Dallas'") - rev(raw, *JUL, "branch_name = 'Dallas'")
    share = dallas / total
    assert 0.4 <= share <= 0.65
    # Dallas is the single largest branch contributor, by a wide margin.
    rows = raw.execute(
        """
        SELECT branch_name,
               sum(net_amount) FILTER (WHERE order_date BETWEEN '2026-08-01' AND '2026-08-31')
             - sum(net_amount) FILTER (WHERE order_date BETWEEN '2026-07-01' AND '2026-07-31') AS chg
        FROM sales GROUP BY 1 ORDER BY chg
        """
    ).fetchall()
    assert rows[0][0] == "Dallas"
    assert rows[0][1] < 3 * rows[1][1]
    orders = raw.execute(
        """
        SELECT count(DISTINCT order_id) FILTER (WHERE order_date BETWEEN '2026-08-01' AND '2026-08-31')
             / count(DISTINCT order_id) FILTER (WHERE order_date BETWEEN '2026-07-01' AND '2026-07-31') - 1
        FROM sales WHERE branch_name = 'Dallas'
        """
    ).fetchone()[0]
    assert orders < -0.12, "Dallas order volume collapses"


def test_major_dallas_customer_cut(raw, scenarios):
    mc = scenarios["stories"]["revenue_decline_aug_2026"]["major_customer"]
    cid = mc["customer_id"]
    j = rev(raw, *JUL, f"customer_id = '{cid}'")
    a = rev(raw, *AUG, f"customer_id = '{cid}'")
    assert a / j - 1 < -0.6
    home = raw.execute(
        "SELECT DISTINCT home_branch_id, segment FROM customers WHERE customer_id = ?", [cid]
    ).fetchall()
    assert home == [("BR-DAL", "Enterprise")]
    worst = raw.execute(
        """
        SELECT customer_id,
               sum(net_amount) FILTER (WHERE order_date BETWEEN '2026-08-01' AND '2026-08-31')
             - sum(net_amount) FILTER (WHERE order_date BETWEEN '2026-07-01' AND '2026-07-31') AS chg
        FROM sales GROUP BY 1 ORDER BY chg LIMIT 1
        """
    ).fetchone()
    assert worst[0] == cid


def test_mix_shift_discount_and_offsetting_category(raw):
    def share_entry(start, end):
        return raw.execute(
            "SELECT sum(quantity) FILTER (WHERE tier = 'Entry') / sum(quantity) FROM sales "
            "WHERE order_date BETWEEN ?::DATE AND ?::DATE",
            [start, end],
        ).fetchone()[0]

    assert share_entry(*AUG) - share_entry(*JUL) > 0.05

    def asp(start, end):
        return raw.execute(
            "SELECT sum(net_amount) / sum(quantity) FROM sales WHERE order_date BETWEEN ?::DATE AND ?::DATE",
            [start, end],
        ).fetchone()[0]

    assert asp(*AUG) / asp(*JUL) - 1 < -0.02

    def disc(start, end):
        return raw.execute(
            "SELECT sum(discount_amount) / sum(gross_amount) FROM sales WHERE order_date BETWEEN ?::DATE AND ?::DATE",
            [start, end],
        ).fetchone()[0]

    assert disc(*AUG) - disc(*JUL) > 0.015
    cats = raw.execute(
        """
        SELECT category,
               sum(net_amount) FILTER (WHERE order_date BETWEEN '2026-08-01' AND '2026-08-31')
             / sum(net_amount) FILTER (WHERE order_date BETWEEN '2026-07-01' AND '2026-07-31') - 1 AS pct
        FROM sales GROUP BY 1 ORDER BY pct DESC
        """
    ).fetchall()
    assert cats[0][0] == "Insulation" and cats[0][1] > 0.1
    assert all(p < 0.02 for c, p in cats[1:]), "only Insulation grows; it partially offsets"


def test_margin_compression_with_flat_revenue(raw):
    q2_26, q2_25 = ("2026-04-01", "2026-06-30"), ("2025-04-01", "2025-06-30")
    assert rev(raw, *q2_26) / rev(raw, *q2_25) - 1 == pytest.approx(0.003, abs=0.01)

    def gm(start, end, where="TRUE"):
        return raw.execute(
            f"SELECT 1 - sum(cost_amount) / sum(net_amount) FROM sales "
            f"WHERE order_date BETWEEN ?::DATE AND ?::DATE AND ({where})",
            [start, end],
        ).fetchone()[0]

    assert gm(*q2_26) - gm(*q2_25) < -0.02
    for c in ("Lumber", "Roofing"):
        drop = gm(*q2_26, f"category = '{c}'") - gm(*q2_25, f"category = '{c}'")
        assert drop < -0.05, c
    other = gm(*q2_26, "category NOT IN ('Lumber', 'Roofing')") - gm(
        *q2_25, "category NOT IN ('Lumber', 'Roofing')"
    )
    assert other > -0.03
    disc = raw.execute(
        """
        SELECT sum(discount_amount) FILTER (WHERE order_date BETWEEN '2026-04-01' AND '2026-06-30')
             / sum(gross_amount) FILTER (WHERE order_date BETWEEN '2026-04-01' AND '2026-06-30')
             - sum(discount_amount) FILTER (WHERE order_date BETWEEN '2025-04-01' AND '2025-06-30')
             / sum(gross_amount) FILTER (WHERE order_date BETWEEN '2025-04-01' AND '2025-06-30')
        FROM sales JOIN (SELECT DISTINCT customer_id, segment FROM customers) USING (customer_id)
        WHERE segment = 'Contractor'
        """
    ).fetchone()[0]
    assert disc > 0.02


def test_conversion_decline_concentrated(raw):
    def rate(start, end, where="TRUE"):
        return raw.execute(
            f"SELECT sum(is_converted) / count(*), count(*) FROM leads "
            f"WHERE created_date BETWEEN ?::DATE AND ?::DATE AND ({where})",
            [start, end],
        ).fetchone()

    q2, q1 = ("2026-04-01", "2026-06-30"), ("2026-01-01", "2026-03-31")
    (r2, n2), (r1, n1) = rate(*q2), rate(*q1)
    assert abs(n2 / n1 - 1) < 0.05, "lead volume stable"
    assert r2 - r1 < -0.02
    cell = "region = 'Gulf Coast' AND channel = 'Paid Search'"
    (c2, _), (c1, _) = rate(*q2, cell), rate(*q1, cell)
    assert c2 - c1 < -0.15
    (o2, _), (o1, _) = rate(*q2, f"NOT ({cell})"), rate(*q1, f"NOT ({cell})")
    assert abs(o2 - o1) < 0.015, "the rest of the funnel is steady"


def test_inventory_buildup_from_slow_skus(raw, scenarios):
    story = scenarios["stories"]["inventory_buildup_2026"]
    ids = story["slow_moving_skus"]["product_ids"]
    assert len(ids) == 15
    v = dict(
        raw.execute(
            "SELECT snapshot_date::VARCHAR, sum(inventory_value) FROM inventory "
            "WHERE snapshot_date IN ('2025-06-30', '2026-06-30') GROUP BY 1"
        ).fetchall()
    )
    assert v["2026-06-30"] / v["2025-06-30"] - 1 > 0.15
    slow = raw.execute(
        "SELECT sum(inventory_value) FROM inventory WHERE snapshot_date = '2026-06-30' AND product_id IN "
        "(SELECT unnest(?::VARCHAR[]))",
        [ids],
    ).fetchone()[0]
    assert slow / (v["2026-06-30"] - v["2025-06-30"]) > 0.6
    q2 = rev(raw, "2026-04-01", "2026-06-30") / rev(raw, "2025-04-01", "2025-06-30") - 1
    assert abs(q2) < 0.015, "sales flat while inventory climbs"
    units = raw.execute(
        "SELECT sum(quantity) FROM sales WHERE order_date BETWEEN '2026-04-01' AND '2026-06-30' "
        "AND product_id IN (SELECT unnest(?::VARCHAR[]))",
        [ids],
    ).fetchone()[0]
    assert units < 200, "the build-up SKUs barely sell"


Q2 = ("2026-04-01", "2026-06-30")


def q2(raw, sql: str) -> float:
    """One number over Q2 2026; every `?` pair in `sql` is the Q2 window."""
    return float(raw.execute(sql, [*Q2] * sql.count("BETWEEN")).fetchone()[0] or 0.0)


def test_flagship_revenue_on_plan_operating_profit_well_below(raw, scenarios):
    story = scenarios["stories"]["operating_profit_miss_q2_2026"]
    rev_a = rev(raw, *Q2)
    rev_p = q2(raw, "SELECT sum(plan_revenue) FROM sales_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE")
    assert rev_a / rev_p - 1 == pytest.approx(0.004, abs=0.005), "revenue target essentially hit"
    quota = q2(raw, "SELECT sum(revenue_target) FROM targets WHERE target_month BETWEEN ?::DATE AND ?::DATE")
    assert quota == pytest.approx(rev_p, abs=1.0), "FY26 quotas allocate the plan"
    gm_a = rev_a - q2(raw, "SELECT sum(cost_amount) FROM sales WHERE order_date BETWEEN ?::DATE AND ?::DATE")
    gm_p = rev_p - q2(
        raw, "SELECT sum(plan_cogs) FROM sales_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE"
    )
    freight_a = q2(
        raw,
        "SELECT sum(freight_cost) FROM orders WHERE status <> 'cancelled' AND order_date BETWEEN ?::DATE AND ?::DATE",
    )
    comm_a = q2(raw, "SELECT sum(commission_amount) FROM sales WHERE order_date BETWEEN ?::DATE AND ?::DATE")
    branch_a = q2(
        raw,
        "SELECT sum(payroll + overtime + occupancy + other_opex) FROM opex "
        "WHERE expense_month BETWEEN ?::DATE AND ?::DATE",
    )
    opex_p = q2(
        raw,
        "SELECT sum(plan_freight_cost + plan_payroll + plan_overtime + plan_occupancy + plan_other_opex) "
        "FROM opex_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE",
    ) + q2(
        raw, "SELECT sum(plan_sales_commission) FROM sales_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE"
    )
    opex_a = freight_a + comm_a + branch_a
    op_a, op_p = gm_a - opex_a, gm_p - opex_p
    assert 0.09 <= op_p / rev_p <= 0.12, "plan operating margin is realistic for a distributor"
    assert -0.5 <= op_a / op_p - 1 <= -0.4, "operating profit misses plan badly"
    gap = op_a - op_p
    assert 0.65 <= (gm_a - gm_p) / gap <= 0.75, "gross margin explains most of the miss"
    assert 0.25 <= (op_a - op_p - (gm_a - gm_p)) / gap <= 0.35, "operating expenses are a material share"
    h = story["headline"]
    assert h["revenue_actual"] == pytest.approx(rev_a, abs=0.01)
    assert h["revenue_plan"] == pytest.approx(rev_p, abs=0.01)
    assert h["operating_profit_actual"] == pytest.approx(op_a, abs=0.05)
    assert h["operating_profit_plan"] == pytest.approx(op_p, abs=0.05)
    assert story["bridge"]["gross_margin"]["variance"] == pytest.approx(gm_a - gm_p, abs=0.05)
    assert story["bridge"]["reconciles"] and story["premise"]["verdict"] == "supported"


def test_flagship_gross_margin_bridge(raw, scenarios):
    """Unit cost and discount effects per (category, segment, region) cell, per list-price dollar."""
    cells = raw.execute(
        """
        WITH cust AS (SELECT DISTINCT customer_id, segment FROM customers),
        a AS (
            SELECT s.category, c.segment, s.region, sum(s.gross_amount) AS g, sum(s.net_amount) AS n,
                   sum(s.cost_amount) AS k
            FROM sales s JOIN cust c USING (customer_id)
            WHERE s.order_date BETWEEN ?::DATE AND ?::DATE GROUP BY ALL
        ), p AS (
            SELECT category, segment, region, sum(plan_gross_revenue) AS g, sum(plan_revenue) AS n,
                   sum(plan_cogs) AS k
            FROM sales_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE GROUP BY ALL
        )
        SELECT category, segment, region, a.g * p.k / p.g - a.k AS cost_effect, a.n - a.g * p.n / p.g AS disc_effect,
               (a.n - a.k) - (p.n - p.k) AS gm_var
        FROM a JOIN p USING (category, segment, region)
        """,
        [*Q2, *Q2],
    ).fetchall()
    assert len(cells) == 10 * 3 * 5, "every cell exists on both sides"
    cost = sum(c[3] for c in cells)
    disc = sum(c[4] for c in cells)
    mix = sum(c[5] for c in cells) - cost - disc
    assert cost < disc < 0 < mix, "unit cost is the larger cause, discounting second, mix a favorable offset"
    by_cat: dict[str, float] = {}
    contractor_by_region: dict[str, float] = {}
    by_seg: dict[str, float] = {}
    for category, segment, region, ce, de, _ in cells:
        by_cat[category] = by_cat.get(category, 0.0) + ce
        by_seg[segment] = by_seg.get(segment, 0.0) + de
        if segment == "Contractor":
            contractor_by_region[region] = contractor_by_region.get(region, 0.0) + de
    ranked = sorted(by_cat, key=by_cat.get)
    assert ranked[:2] == ["Lumber", "Roofing"]
    assert (by_cat["Lumber"] + by_cat["Roofing"]) / cost > 0.9, "the rest of the catalog is near plan cost"
    assert by_seg["Contractor"] / disc > 0.95, "discounting is a Contractor story"
    assert min(contractor_by_region, key=contractor_by_region.get) == "Gulf Coast"
    assert contractor_by_region["Gulf Coast"] / by_seg["Contractor"] > 0.35
    b = scenarios["stories"]["operating_profit_miss_q2_2026"]["gross_margin_bridge"]
    assert b["unit_cost_effect"] == pytest.approx(cost, abs=0.05)
    assert b["discount_effect"] == pytest.approx(disc, abs=0.05)
    assert b["mix_effect"] == pytest.approx(mix, abs=0.1)
    assert b["reconciles"]


def test_flagship_operating_expense_overruns(raw, scenarios):
    def line(actual_sql: str, plan_sql: str) -> float:
        return q2(raw, actual_sql) - q2(raw, plan_sql)

    plan = "SELECT sum({}) FROM opex_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE"
    branch = "SELECT sum({}) FROM opex WHERE expense_month BETWEEN ?::DATE AND ?::DATE"
    freight = line(
        "SELECT sum(freight_cost) FROM orders WHERE status <> 'cancelled' AND order_date BETWEEN ?::DATE AND ?::DATE",
        plan.format("plan_freight_cost"),
    )
    over = {
        k: line(branch.format(k), plan.format(f"plan_{k}"))
        for k in ("payroll", "overtime", "occupancy", "other_opex")
    }
    assert freight > over["overtime"] > 50_000, "freight is the largest overrun, overtime second"
    assert min(over["payroll"], over["occupancy"]) <= 0, "at least one line is on or under plan"
    ot = raw.execute(
        """
        SELECT b.region, sum(x.overtime) - (SELECT sum(p.plan_overtime) FROM opex_plan p JOIN branches b2 USING (branch_id)
                                            WHERE b2.region = b.region AND p.plan_month BETWEEN ?::DATE AND ?::DATE)
        FROM opex x JOIN branches b USING (branch_id) WHERE x.expense_month BETWEEN ?::DATE AND ?::DATE
        GROUP BY 1 ORDER BY 2 DESC
        """,
        [*Q2, *Q2],
    ).fetchall()
    assert ot[0][0] == "Central Texas" and ot[0][1] / over["overtime"] > 0.8
    lines = {
        x["metric"]: x
        for x in scenarios["stories"]["operating_profit_miss_q2_2026"]["operating_expense_lines"]
    }
    assert lines["freight_cost"]["variance"] == pytest.approx(freight, abs=0.05)
    assert lines["overtime"]["variance"] == pytest.approx(over["overtime"], abs=0.05)


def test_flagship_freight_overrun_is_the_fuel_surcharge(raw, scenarios):
    bad = raw.execute(
        "SELECT count(*) FROM orders WHERE abs(freight_cost - (base_freight + fuel_surcharge)) > 0.005"
    ).fetchone()[0]
    assert bad == 0, "freight is base freight plus fuel surcharge on every order"
    a = raw.execute(
        """
        SELECT sum(base_freight), sum(fuel_surcharge), count(*) FILTER (WHERE channel <> 'Counter')
        FROM orders WHERE status <> 'cancelled' AND order_date BETWEEN ?::DATE AND ?::DATE
        """,
        [*Q2],
    ).fetchone()
    p = raw.execute(
        "SELECT sum(plan_base_freight), sum(plan_fuel_surcharge), sum(plan_deliveries) FROM opex_plan "
        "WHERE plan_month BETWEEN ?::DATE AND ?::DATE",
        [*Q2],
    ).fetchone()
    base_var, fuel_var = a[0] - p[0], a[1] - p[1]
    assert fuel_var / (base_var + fuel_var) > 0.9, "the fuel surcharge is nearly all of the freight overrun"
    assert abs(a[0] / p[0] - 1) < 0.02, "base freight on plan"
    assert abs(a[2] / p[2] - 1) < 0.03, "delivery volume on plan"
    f = scenarios["stories"]["operating_profit_miss_q2_2026"]["freight"]
    assert f["fuel_surcharge"]["variance"] == pytest.approx(fuel_var, abs=0.05)
    assert f["deliveries"]["actual"] == a[2]


def test_flagship_labor_overrun_and_offset_in_the_same_branches(raw, scenarios):
    rows = raw.execute(
        """
        WITH a AS (SELECT branch_id, sum(payroll) AS pay, sum(overtime) AS ot, avg(headcount) AS fte
                   FROM opex WHERE expense_month BETWEEN ?::DATE AND ?::DATE GROUP BY 1),
             p AS (SELECT branch_id, sum(plan_payroll) AS pay, sum(plan_overtime) AS ot, avg(plan_headcount) AS fte
                   FROM opex_plan WHERE plan_month BETWEEN ?::DATE AND ?::DATE GROUP BY 1)
        SELECT b.branch_name, a.ot - p.ot, a.pay - p.pay, a.fte - p.fte
        FROM a JOIN p USING (branch_id) JOIN branches b USING (branch_id) ORDER BY 2 DESC
        """,
        [*Q2, *Q2],
    ).fetchall()
    top = rows[:2]
    assert {r[0] for r in top} == {"San Antonio", "Austin"}
    total_ot = sum(r[1] for r in rows)
    assert sum(r[1] for r in top) / total_ot > 0.9, "the overtime overrun is concentrated in two branches"
    for _, ot, pay, fte in top:
        assert ot > 100_000 and pay < 0 and fte <= -1, "open positions: payroll and headcount below plan"
    assert sum(r[1] + r[2] for r in rows) > 0, "labor is a net overrun"
    labor = scenarios["stories"]["operating_profit_miss_q2_2026"]["labor"]
    assert labor["overtime"]["variance"] == pytest.approx(total_ot, abs=0.05)


def test_flagship_lumber_cost_increase_comes_from_one_supplier(raw, scenarios):
    """Each supplier at its own prior-year cost per list dollar, scaled by its plan cell's change."""

    def split(category: str) -> dict[str, float]:
        rows = raw.execute(
            """
            WITH cust AS (SELECT DISTINCT customer_id, segment FROM customers),
            s AS (SELECT l.*, c.segment, sup.supplier_name FROM sales l JOIN cust c USING (customer_id)
                  JOIN products p USING (product_id) JOIN suppliers sup ON sup.supplier_id = p.supplier_id
                  WHERE l.category = ?),
            p AS (SELECT segment, region, sum(plan_cogs) / sum(plan_gross_revenue) AS r FROM sales_plan
                  WHERE category = ? AND plan_month BETWEEN '2026-04-01' AND '2026-06-30' GROUP BY ALL),
            pyc AS (SELECT segment, region, sum(cost_amount) / sum(gross_amount) AS r FROM s
                    WHERE order_date BETWEEN '2025-04-01' AND '2025-06-30' GROUP BY ALL),
            pys AS (SELECT segment, region, supplier_name, sum(cost_amount) / sum(gross_amount) AS r FROM s
                    WHERE order_date BETWEEN '2025-04-01' AND '2025-06-30' GROUP BY ALL)
            SELECT supplier_name, sum(gross_amount * p.r * coalesce(pys.r / pyc.r, 1)) - sum(cost_amount),
                   sum(cost_amount)
            FROM s JOIN p USING (segment, region) LEFT JOIN pyc USING (segment, region)
            LEFT JOIN pys USING (segment, region, supplier_name)
            WHERE order_date BETWEEN '2026-04-01' AND '2026-06-30' GROUP BY 1
            """,
            [category, category],
        ).fetchall()
        return {r[0]: (r[1], r[1] / r[2]) for r in rows}  # effect, effect per COGS dollar

    lumber = split("Lumber")
    total = sum(e for e, _ in lumber.values())
    gulf, piney = lumber["Gulf States Forest Products"], lumber["Piney Woods Timber"]
    assert gulf[0] / total > 0.75 and piney[1] - gulf[1] > 0.07, "one supplier's increase explains Lumber"
    roofing = [r for _, r in split("Roofing").values()]
    assert max(roofing) - min(roofing) < 0.02, "Roofing's increase is broad across suppliers"
    key = {
        r["supplier"]: r["effect"]
        for r in scenarios["stories"]["operating_profit_miss_q2_2026"]["lumber_unit_cost_by_supplier"]
    }
    assert key["Gulf States Forest Products"] == pytest.approx(gulf[0], abs=0.05)
    # The raw free-text supplier keeps its messy casing for the DQ rules to find.
    upper = raw.execute("SELECT count(*) FROM products WHERE supplier = upper(supplier)").fetchone()[0]
    assert upper > 0


def test_flagship_revenue_mix_and_related_signal(raw, scenarios):
    cells = dict(
        (tuple(r[:2]), r[2])
        for r in raw.execute(
            """
            WITH cust AS (SELECT DISTINCT customer_id, segment FROM customers),
            a AS (SELECT region, c.segment, sum(net_amount) AS n FROM sales s JOIN cust c USING (customer_id)
                  WHERE order_date BETWEEN ?::DATE AND ?::DATE GROUP BY ALL),
            p AS (SELECT region, segment, sum(plan_revenue) AS n FROM sales_plan
                  WHERE plan_month BETWEEN ?::DATE AND ?::DATE GROUP BY ALL)
            SELECT region, segment, a.n / p.n - 1 FROM a JOIN p USING (region, segment)
            """,
            [*Q2, *Q2],
        ).fetchall()
    )
    assert cells[("Gulf Coast", "Contractor")] < -0.1, "Gulf Coast Contractor far below plan"
    assert cells[("Central Texas", "Contractor")] < 0
    ent = raw.execute(
        """
        SELECT (SELECT sum(net_amount) FROM sales s JOIN (SELECT DISTINCT customer_id, segment FROM customers) c
                USING (customer_id) WHERE c.segment = 'Enterprise' AND order_date BETWEEN ?::DATE AND ?::DATE)
             / (SELECT sum(plan_revenue) FROM sales_plan WHERE segment = 'Enterprise'
                AND plan_month BETWEEN ?::DATE AND ?::DATE) - 1
        """,
        [*Q2, *Q2],
    ).fetchone()[0]
    assert ent > 0, "Enterprise above plan"
    resp = raw.execute(
        """
        SELECT median(first_response_hours) FILTER (WHERE created_date BETWEEN '2026-04-01' AND '2026-06-30'),
               median(first_response_hours) FILTER (WHERE created_date BETWEEN '2026-01-01' AND '2026-03-31')
        FROM leads WHERE region = 'Gulf Coast' AND channel = 'Paid Search'
        """
    ).fetchone()
    assert resp[0] > 48 and resp[1] < 8
    signal = scenarios["stories"]["operating_profit_miss_q2_2026"]["related_signals"][0]
    assert signal["relationship_to_gap"] == "not_established"
    assert signal["median_first_response_hours_current"] == pytest.approx(resp[0], abs=0.05)
    assert signal["median_first_response_hours_baseline"] == pytest.approx(resp[1], abs=0.05)


def test_sales_plan_rows_are_internally_consistent(raw):
    bad = raw.execute(
        "SELECT count(*) FROM sales_plan WHERE abs(plan_revenue - (plan_gross_revenue - plan_discount_amount)) > 0.011"
    ).fetchone()[0]
    assert bad == 0
    versions = raw.execute(
        "SELECT DISTINCT plan_version FROM sales_plan UNION SELECT DISTINCT plan_version FROM opex_plan"
    )
    assert [r[0] for r in versions.fetchall()] == ["FY26 Operating Plan"]
    months = raw.execute(
        "SELECT count(DISTINCT plan_month), min(plan_month)::VARCHAR FROM sales_plan"
    ).fetchone()
    assert months == (12, "2026-01-01")


def test_answer_key_is_complete(scenarios):
    stories = scenarios["stories"]
    assert list(stories)[0] == "operating_profit_miss_q2_2026", "the flagship story comes first"
    assert set(stories) == {
        "operating_profit_miss_q2_2026",
        "revenue_decline_aug_2026",
        "margin_compression_q2_2026",
        "conversion_decline_q2_2026",
        "inventory_buildup_2026",
    }
    for s in stories.values():
        assert s["questions"] and s["expected_discoveries"] and s["headline"]
    yoy = stories["revenue_decline_aug_2026"]["yoy"]
    assert yoy["baseline_period"]["start"] == "2025-08-01"
