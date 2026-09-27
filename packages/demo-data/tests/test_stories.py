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


def test_forecast_miss_concentrated_in_contractor(raw):
    rows = raw.execute(
        """
        WITH a AS (
            SELECT c.segment, sum(net_amount) AS actual FROM sales s
            JOIN (SELECT DISTINCT customer_id, segment FROM customers) c USING (customer_id)
            WHERE order_date BETWEEN '2026-04-01' AND '2026-06-30' GROUP BY 1
        ), f AS (
            SELECT segment, sum(forecast_revenue) AS fc FROM forecast
            WHERE forecast_month BETWEEN '2026-04-01' AND '2026-06-01' GROUP BY 1
        )
        SELECT segment, actual, fc FROM a JOIN f USING (segment)
        """
    ).fetchall()
    actual = sum(r[1] for r in rows)
    fc = sum(r[2] for r in rows)
    miss = actual - fc
    assert actual / fc - 1 < -0.03
    contractor = next(r for r in rows if r[0] == "Contractor")
    assert (contractor[1] - contractor[2]) / miss > 0.6


def test_answer_key_is_complete(scenarios):
    stories = scenarios["stories"]
    assert set(stories) == {
        "revenue_decline_aug_2026",
        "margin_compression_q2_2026",
        "conversion_decline_q2_2026",
        "inventory_buildup_2026",
        "forecast_miss_q2_2026",
    }
    for s in stories.values():
        assert s["questions"] and s["expected_discoveries"] and s["headline"]
    yoy = stories["revenue_decline_aug_2026"]["yoy"]
    assert yoy["baseline_period"]["start"] == "2025-08-01"
