# Demo data and planted scenarios: Summit Supply Co.

Summit Supply Co. is a fictional B2B building-materials distributor with 14 branches in Texas and
Oklahoma. The demo dataset is generated, not hand-written: `analystos_demo.generate(out_dir, seed=42)`
writes two years of realistic, deliberately messy operating data and an answer key
(`scenarios.json`) measured from the files it just wrote.

```bash
make demo                                   # generate (or verify the cached copy) and print the stories
uv run python -m analystos_demo generate --out /tmp/summit --seed 42
uv run python -m analystos_demo scenarios   # headline numbers from the answer key
```

Generation takes about 12 seconds. The same seed always produces byte-identical files and the same
manifest content hash (`packages/demo-data/tests/test_generator.py` checks this, and that the stories
still hold for a different seed).

## What is in the dataset

| Table | File | Rows (seed 42) | Notes |
|---|---|---:|---|
| regions | regions.csv | 5 | Conformed dimension: North Texas, Gulf Coast, Central Texas, West Texas, Oklahoma |
| customer_segments | customer_segments.csv | 3 | Conformed dimension: Enterprise, Contractor, Retail |
| branches | branches.csv | 14 | Plano opened May 2025 (a "new store") |
| sales_reps | sales_reps.csv | 74 | |
| customers | customers.csv | 1,508 | 1,500 accounts plus 8 duplicate rows |
| products | products.csv | 400 | 10 categories, Entry / Standard / Premium tiers |
| product_costs | product_costs.csv | 9,600 | Monthly unit cost per product |
| orders | orders.csv | 130,437 | 2024-10-01 to 2026-09-30, status completed / open / cancelled |
| order_lines | order_lines.parquet | 376,072 | quantity, list price, discount, net amount, unit cost, commission |
| returns | returns.csv | 6,324 | |
| inventory_snapshots | inventory_snapshots.parquet | 124,175 | Month-end on hand by branch and product, trailing 90-day units sold |
| budgets_branch, budgets_category | budgets.xlsx | 336, 240 | One messy two-sheet workbook |
| targets | targets.csv | 1,776 | Monthly revenue target per rep |
| leads | leads.csv | 15,399 | Channel, region, segment, response time, conversion |
| opportunities | opportunities.json | 7,225 | JSON records |
| revenue_forecast | revenue_forecast.csv | 180 | FY26 plan by month x region x segment |
| operating_expenses | operating_expenses.csv | 329 | Branch opex by month |

### Deliberate mess

Real data is not clean, and AnalystOS is supposed to find problems rather than hide them. The
loader keeps every one of these as-is; the demo DQ rules (`data/dq_rules.yaml`) flag most of them.

| Where | Problem | Count (seed 42) |
|---|---|---:|
| customers.customer_id | Exact duplicate rows (inactive accounts, so no join inflates revenue) | 8 |
| customers.region | Null billing region | 40 |
| customers.region | Inconsistent casing (`north texas`, `GULF COAST`) | 56 |
| customers.account_opened | Malformed dates (`03/15/2025`, `2025-02-30`, `N/A`, `TBD`, ...) | 145 |
| customers.customer_name | A prompt-injection string stored as a customer name | 1 |
| order_lines.quantity | Negative quantities (returns keyed as orders) | 48 |
| orders.status | Cancelled orders whose lines still carry amounts | 3,889 |
| returns.order_id | Whitespace-padded IDs that do not join without `trim()` | 742 |
| products.supplier | Upper-cased supplier names | 26 |
| budgets.xlsx | Title rows, blank separator row between fiscal years, notes row; header on row 5 / row 4 | n/a |

Canonical Region comes from the fulfilling branch (`branches.region` -> `regions`); the messy
billing region in `customers.csv` is not used for reporting. The `returns -> orders` relationship is
deliberately left unapproved because of the padded IDs; returns carry branch, product and customer.

## How the stories are planted

Every story is a real causal change inside the generator: fewer orders, a cheaper tier mix, more
discount, higher unit costs, stalled SKUs, unworked leads. Two knobs are calibrated exactly:

* **August 2026.** July and August candidate orders are over-drawn by 12%, then thinned per branch
  (using pre-drawn uniforms) so Dallas lands on its target and every other branch lands on a common
  change plus a small spread; the common change is solved by bisection so total August revenue is
  exactly 11.8% below July. The rounding residual of whole orders is carried from branch to branch.
* **Q2 2026.** Contractor orders in Gulf Coast and Central Texas are thinned so Q2 2026 revenue is
  +0.3% versus Q2 2025, which makes revenue flat, causes the forecast miss and leaves margin to explain.

After writing the files, `analystos_demo.measure` re-measures everything with DuckDB SQL over the
files and writes `scenarios.json`. The answer key therefore can never drift from the data.

Unless stated otherwise, **Revenue** means `SUM(order_lines.net_amount)` over lines of orders whose
status is not `cancelled`, dated by `orders.order_date`. The figures below are for seed 42.

## Story 1: August 2026 revenue decline (acceptance flow 1)

**Question:** "Why was August revenue down?" (today = 2026-09-18, so "August" is August 2026,
compared with July 2026).

| Measure | July 2026 | August 2026 | Change |
|---|---:|---:|---:|
| Revenue | $11,365,541 | $10,024,462 | **-11.80%** (-$1,341,079) |
| Orders | | | -6.7% |
| AOV | | | -5.5% |
| Units | | | -8.2% |
| ASP | | | -3.9% |
| Discount rate | 9.68% | 11.64% | +1.96 pp |
| Entry-tier share of units | 28.6% | 36.7% | +8.0 pp |

Drivers, each discoverable with a real query:

* **Order volume is the larger component.** LMDI decomposition: orders explain $741.6k and AOV
  $599.5k of the $1.34M decline (Revenue = Orders x AOV); units $911.5k and ASP $429.6k
  (Revenue = Units x ASP).
* **Dallas explains about half.** Dallas revenue -26.7% ($2.48M to $1.82M), **49.4% of the
  decline**, with Dallas order count -21.1%. The next largest branch (Austin) explains 8%.
* **One major Dallas customer cut purchases.** Trinity Ridge Construction (Enterprise, Dallas):
  $394.2k to $45.4k (-88.5%), **26.0% of the company decline** and 52.7% of the Dallas decline. It is
  the largest customer-level decline.
* **Mix shifted toward entry-level products**, lowering ASP.
* **Discounting increased** (a late-summer promotion on about 40% of August lines).
* **Insulation grew and partially offset the decline:** +18.8% ($1.01M to $1.20M), offsetting 14%
  of the decline. It is the only growing category month over month.
* **Year over year** August 2026 is -10.1% versus August 2025; Insulation is the fastest-growing
  category YoY (+51%).

## Story 2: margin compression with flat revenue (acceptance flow 2)

**Question:** "Revenue was roughly flat in Q2 2026 versus Q2 2025. Why did margin decline?"
"Margin" is deliberately ambiguous: the semantic model defines Gross Margin, Gross Margin %,
Contribution Margin and Operating Margin, and the glossary term "Margin" lists all four, so AnalystOS
must ask. The answer key uses Gross Margin %.

* Revenue $33.78M to $33.89M (**+0.30%**, flat).
* Gross margin % **26.34% to 21.58% (-4.77 pp)**; gross margin dollars $8.90M to $7.31M.
* Counterfactual attribution: unit-cost inflation **-4.21 pp** (Q2 2026 lines at the same product's
  cost 12 months earlier), discounting **-1.61 pp** (Q2 2026 lines at Q2 2025 segment discount
  rates), residual mix and other effects +1.05 pp.
* Cause 1: supplier cost increases in **Lumber (+10.3% unit cost)** and **Roofing (+8.6%)** from
  March 2026, not passed through to list prices. Lumber GM% -8.8 pp, Roofing -7.5 pp, other
  categories about -1.7 to -2.6 pp (general inflation outpacing the 1.5% list-price increase).
* Cause 2: **Contractor discounting** up 3.4 pp (6.96% to 10.38%) from April 2026 to defend volume;
  Enterprise and Retail discount rates unchanged.

## Story 3: conversion decline concentrated in one region and channel

**Question:** "Why did conversion rate decline in Q2 2026?" (lead cohorts Q2 2026 vs Q1 2026; both
cohorts have had at least 90 days to convert by 2026-09-30).

* Lead volume flat: 1,919 to 1,922 (+0.2%).
* Conversion rate 26.3% to 23.3% (**-3.06 pp**).
* **Gulf Coast x Paid Search** conversion fell from 30.0% to 5.5%; restoring that one cell's Q1 rate
  explains **91%** of the decline. Median first-response time for those leads went from 4.2 hours to
  70.7 hours (routed to an unmonitored queue after a CRM change), which the data also shows.

## Story 4: inventory build-up while sales are flat

**Question:** "Why did inventory value increase in June 2026 compared with June 2025?" or "Which
inventory is unhealthy?"

* Inventory value at month end: $13.19M (2025-06-30) to $17.30M (2026-06-30), **+31.1%**, while Q2
  revenue is flat year over year (+0.3%).
* **15 slow-moving SKUs** (the "Summit Select Signature" premium line in Plumbing, Electrical and
  Tools, launched 2025-09-15 and stocked in every branch) hold $3.18M at 2026-06-30 from zero a year
  earlier: **77% of the increase**. They sold 51, 89 and 103 units in Q4 2025, Q1 2026 and Q2 2026.
* They top the days-of-supply ranking (tens of thousands of days, or no sales at all in 90 days).
  Inventory Value is semi-additive: compare single snapshot dates, never sum months.

## Story 5: forecast miss concentrated in specific segments

**Question:** "Why was Q2 2026 revenue below forecast?" (FY26 plan forecast, made in December 2025
as prior-year actual x 1.05).

* Actual $33.89M vs forecast $35.47M: **-$1.59M (-4.5%)**.
* By segment: **Contractor -$1.67M (-8.2%), 105% of the miss**; Retail -$0.25M; Enterprise beat
  forecast by $0.33M.
* By cell: **Contractor in Gulf Coast (-24.3%, 69% of the miss) and Central Texas (-15.1%, 43%)**,
  a housing slowdown the plan did not anticipate. Together they account for 112% of the miss.

## Verification

* `packages/demo-data/tests/test_stories.py` re-derives every magnitude above with independent
  DuckDB SQL over the raw files (not through `analystos_demo.measure`).
* `tests/evals/test_investigations.py` runs the investigator on each story and scores whether the
  expected evidence appears (see [analytical-correctness.md](analytical-correctness.md)).
