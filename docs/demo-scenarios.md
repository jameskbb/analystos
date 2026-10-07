# Demo data and planted scenarios: Summit Supply Co.

Summit Supply Co. is a fictional B2B building-materials distributor with 14 branches in Texas and
Oklahoma. The demo dataset is generated, not hand-written: `analystos_demo.generate(out_dir, seed=42)`
writes two years of realistic, deliberately messy operating data and an answer key
(`scenarios.json`) measured from the files it just wrote. The flagship is Story 0: Q2 2026 revenue
landed on the FY26 Operating Plan while operating profit missed it by 49%, and the answer key
reconciles that gap to gross margin (one Lumber supplier's price increase, Roofing costs, Contractor
discounting in Gulf Coast, a favorable mix) and operating expenses (a carrier fuel surcharge, overtime
covering open positions in two branches).

```bash
make demo                                   # generate (or verify the cached copy) and print the stories
uv run python -m analystos_demo generate --out /tmp/summit --seed 42
uv run python -m analystos_demo scenarios   # headline numbers from the answer key
```

Generation takes about 10 seconds. The same seed always produces byte-identical files and the same
manifest content hash (`packages/demo-data/tests/test_generator.py` checks this, and that the stories
still hold for a different seed).

## What is in the dataset

| Table | File | Rows (seed 42) | Notes |
|---|---|---:|---|
| regions | regions.csv | 5 | Conformed dimension: North Texas, Gulf Coast, Central Texas, West Texas, Oklahoma |
| customer_segments | customer_segments.csv | 3 | Conformed dimension: Enterprise, Contractor, Retail |
| product_categories | product_categories.csv | 10 | Conformed dimension: category, code, merchandising manager |
| suppliers | suppliers.csv | 15 | Supplier master: supplier_id, clean name, primary category |
| branches | branches.csv | 14 | Plano opened May 2025 (a "new store") |
| sales_reps | sales_reps.csv | 74 | |
| customers | customers.csv | 1,508 | 1,500 accounts plus 8 duplicate rows |
| products | products.csv | 400 | 10 categories, Entry / Standard / Premium tiers; supplier_id plus a messy free-text supplier |
| product_costs | product_costs.csv | 9,600 | Monthly unit cost per product |
| orders | orders.csv | 130,390 | 2024-10-01 to 2026-09-30, status completed / open / cancelled; freight = base freight + fuel surcharge per order |
| order_lines | order_lines.parquet | 375,951 | quantity, list price, discount, net amount, unit cost, commission |
| returns | returns.csv | 6,313 | |
| inventory_snapshots | inventory_snapshots.parquet | 124,169 | Month-end on hand by branch and product, trailing 90-day units sold |
| budgets_branch, budgets_category | budgets.xlsx | 336, 240 | One messy two-sheet workbook; FY26 rows allocate the operating plan |
| targets | targets.csv | 1,776 | Monthly revenue quota per rep; FY26 quotas allocate the operating plan |
| leads | leads.csv | 15,399 | Channel, region, segment, response time, conversion |
| opportunities | opportunities.json | 7,183 | JSON records |
| operating_expenses | operating_expenses.csv | 329 | Branch headcount (FTE), regular payroll, overtime, occupancy and other costs by month |
| sales_plan | sales_plan.csv | 1,800 | FY26 Operating Plan: units, list revenue, discounts, revenue, COGS, commission by month x region x segment x category |
| opex_plan | opex_plan.csv | 168 | FY26 Operating Plan: base freight, fuel surcharge, deliveries, headcount and branch expense lines by month x branch |

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
| returns.order_id | Whitespace-padded IDs that do not join without `trim()` | 741 |
| products.supplier | Upper-cased supplier names (reporting uses `supplier_id` -> `suppliers`) | 26 |
| budgets.xlsx | Title rows, blank separator row between fiscal years, notes row; header on row 5 / row 4 | n/a |

Category is a conformed dimension too (`product_categories`, reached from products, the sales plan
and the category budget), so plan and actuals group by the same governed Region, Segment and
Category. Canonical Region comes from the fulfilling branch (`branches.region` -> `regions`); the
messy billing region in `customers.csv` is not used for reporting. The `returns -> orders` relationship is
deliberately left unapproved because of the padded IDs; returns carry branch, product and customer.

## How the stories are planted

Every story is a real causal change inside the generator: fewer orders, a cheaper tier mix, more
discount, a supplier price increase, a fuel surcharge spike, open positions covered by overtime,
stalled SKUs, unworked leads. Three knobs are calibrated exactly:

* **August 2026.** July and August candidate orders are over-drawn by 12%, then thinned per branch
  (using pre-drawn uniforms) so Dallas lands on its target and every other branch lands on a common
  change plus a small spread; the common change is solved by bisection so total August revenue is
  exactly 11.8% below July. The rounding residual of whole orders is carried from branch to branch.
* **Q2 2026.** Contractor orders in Gulf Coast and Central Texas are thinned so Q2 2026 revenue is
  +0.3% versus Q2 2025, which makes revenue flat and leaves margin to explain.
* **The FY26 Operating Plan.** Contractor volume growth in the plan is solved so the Q2 plan sits
  0.4% below that calibrated Q2 actual. The solve uses only FY25 data, as the real plan would have.

After writing the files, `analystos_demo.measure` re-measures everything with DuckDB SQL over the
files and writes `scenarios.json`. The answer key therefore can never drift from the data.

Unless stated otherwise, **Revenue** means `SUM(order_lines.net_amount)` over lines of orders whose
status is not `cancelled`, dated by `orders.order_date`. The figures below are for seed 42.

### The FY26 Operating Plan

The board approved the plan in December 2025. It is built at month x branch x segment x category
and published at month x region x segment x category (`sales_plan`) and month x branch
(`opex_plan`), with `plan_version = "FY26 Operating Plan"`:

* Each cell starts from the same month of FY25. Volume grows by segment (Enterprise +3%, Retail -3%,
  Contractor solved as above: a housing slowdown the plan anticipated, but not as deep as Gulf
  Coast turned out to be), plus a small FP&A adjustment per branch and segment.
* List prices +1.5% (the January 2026 increase, which happened as planned); unit costs +2%; each
  cell keeps its FY25 discount rate and commission rate.
* Plano opened in May 2025, so it is planned at its Sep-Nov 2025 run rate, seasonally adjusted, plus
  a 50% ramp, instead of from a partial prior year.
* Opex lines are the branch's FY25 month (Plano: its Sep-Nov 2025 run rate) at the same headcount,
  x (payroll +3% merit, overtime flat, occupancy +2.5% lease escalators, other +2%). Base freight and
  deliveries are planned at the branch's FY25 ratios to revenue on its plan revenue (base freight
  plus a 2% carrier rate increase); the fuel surcharge at its FY25 share of base freight (4%).
* There is one plan. The FY26 rows of `budgets.xlsx` are its branch and category allocation (rounded
  to $100) and the FY26 rep quotas in `targets.csv` split each branch's monthly plan revenue, so
  "revenue target", "budget" and "plan" agree for FY26.

### Operating expenses

Operating expenses are freight, labor, sales commission, occupancy and other branch costs:

* **Freight** (`orders.freight_cost`) is every outbound delivery, own fleet and carriers, and equals
  `base_freight + fuel_surcharge` on every order. Base freight is a per-delivery fee plus a share of
  the order value (just under 3% of revenue); the carrier fuel surcharge is normally 4% of base.
* **Labor** is regular payroll (headcount x a loaded monthly cost per FTE) plus overtime (about 7% of
  payroll). Branch staffing (`headcount`) follows square footage and revenue.
* Sales commission is on `order_lines.commission_amount`; occupancy is per square foot; other costs
  have a fixed and a revenue-driven part. Branch cost rates and carrier rates step up in January 2026.

## Story 0: revenue on plan, operating profit well below plan (the flagship)

**Question:** "We made our sales number last quarter, but we missed our target for net profit. What
happened?" ("net profit" resolves to Operating Profit through the glossary term Net Profit: interest and
tax are held at corporate and are not in this dataset.) (today = 2026-09-30, the workspace reference date, so "last quarter" is Q2 2026, April to
June, compared with the FY26
Operating Plan). Operating Profit = Gross Margin - Operating Expenses.

| Measure | Actual | Plan | Variance |
|---|---:|---:|---:|
| Revenue | $33,886,471 | $33,750,469 | **+$136,001 (+0.4%)**: premise supported |
| Gross margin | $7,431,629 | $8,755,362 | -$1,323,733 |
| Operating expenses | $5,498,714 | $4,964,724 | +$533,991 |
| Operating profit | $1,932,915 | $3,790,639 | **-$1,857,724 (-49.0%)** |
| Operating margin | 5.7% | 11.2% | |

The gap reconciles exactly: gross margin explains -$1,323,733 (71%) and operating expenses
-$533,991 (29%). Every material branch ends in a measured operational cause.

**Gross margin bridge.** Per (category, segment, region) cell over the quarter, per list-price
dollar (units of different products are not comparable, so cost per unit would move on product mix
alone):

* unit-cost effect = actual list revenue x (plan COGS / list - actual COGS / list)
* discount effect = actual list revenue x (actual net / list - plan net / list)
* mix, volume and other = the gross margin variance minus both

| Effect | Amount | Where |
|---|---:|---|
| Unit-cost inflation | **-$885,657** | Lumber -$554,991 (cost +7.4% vs plan), Roofing -$296,531 (+6.7%); the other eight categories -$34k together |
| Increased discounting | **-$622,435** | Contractor -$629,489 (discount rate 9.98% vs plan 6.95%); within Contractor, Gulf Coast -$304,004 (48%), then North Texas -$126,003 and Central Texas -$76,172 |
| Mix, volume and other | **+$184,359** | Favorable offset: revenue landed on plan with deeper discounts, so list-price volume ran ahead of plan at plan margins |

**Lumber is one supplier.** Gulf States Forest Products raised prices about 12% in March 2026 (a mill
curtailment); Piney Woods Timber rose about 2.5%. Split below the plan grain (supplier is not in
the plan), with each supplier costed at its own prior-year cost per list dollar scaled by its plan
cell's planned change: Gulf States -$468,391 (84% of Lumber, cost +12.1% vs plan), Piney Woods
-$89,373 (+2.5%), supplier mix +$2,772. Roofing's increase is broad: Red River -$152,930 (+6.8%),
Caprock -$143,161 (+6.5%). Costing every supplier at its cell's blended plan rate instead would
book the suppliers' standing cost differences as cost changes (it would call Roofing 76% Red River).

**Operating expense overruns.**

* **Freight +$336,224 (+35%) is the fuel surcharge.** The carrier fuel surcharge went from 4% to 40%
  of base freight in April 2026: +$332,529, 98.9% of the freight overrun. Base freight is on plan
  (+$3,695, +0.4%) and so is delivery volume (10,664 deliveries vs 10,553 planned, +1.1%).
* **Labor +$196,264: overtime covering open positions.** Overtime +$284,506, of which San Antonio
  +$135,340 and Austin +$141,201. The same two branches are below plan on headcount (10.7 vs 12.3 FTE
  and 11.0 vs 13.3) and on regular payroll (-$26,459 and -$36,827): unfilled yard positions covered
  with overtime. Company payroll -$88,242.
* **Other lines, net +$1,503:** sales commission +$2,425, occupancy on plan, other opex -$922.

**Where revenue landed.** Enterprise +1.5% vs plan, Contractor +0.5%, Retail -3.2%. Gulf Coast
Contractor is 18.8% below plan and Central Texas Contractor 4.3% below, offset by Enterprise in both
regions (+11%) and Contractor elsewhere.

**Related signal, not an explanation.** In the same region and quarter as the Contractor discount
concentration, Gulf Coast Paid Search lead conversion fell from 30.0% to 5.5% and median first
response went from 4.2 hours to 70.7 hours (Q2 2026 vs Q1 2026 lead cohorts; Story 3). It is a
verified operating signal, but the data does not establish it as a cause of the operating profit
miss, and the answer key records it with relationship `not_established`.

## Story 1: August 2026 revenue decline (acceptance flow 1)

**Question:** "Why was August revenue down?" (today = 2026-09-30, so "August" is August 2026,
compared with July 2026).

| Measure | July 2026 | August 2026 | Change |
|---|---:|---:|---:|
| Revenue | $11,395,391 | $10,050,853 | **-11.80%** (-$1,344,538) |
| Orders | | | -6.7% |
| AOV | | | -5.5% |
| Units | | | -8.1% |
| ASP | | | -4.0% |
| Discount rate | 9.51% | 11.49% | +1.97 pp |
| Entry-tier share of units | 28.6% | 36.6% | +8.0 pp |

Drivers, each discoverable with a real query:

* **Order volume is the larger component.** LMDI decomposition: orders explain $737.8k and AOV
  $606.7k of the $1.34M decline (Revenue = Orders x AOV); units $908.1k and ASP $436.5k
  (Revenue = Units x ASP).
* **Dallas explains about half.** Dallas revenue -26.6% ($2.50M to $1.83M), **49.5% of the
  decline**, with Dallas order count -21.1%. The next largest branch (Austin) explains 9%.
* **One major Dallas customer cut purchases.** Trinity Ridge Construction (Enterprise, Dallas):
  $394.2k to $45.4k (-88.5%), **25.9% of the company decline** and 52.4% of the Dallas decline. It is
  the largest customer-level decline.
* **Mix shifted toward entry-level products**, lowering ASP.
* **Discounting increased** (a late-summer promotion on about 40% of August lines).
* **Insulation grew and partially offset the decline:** +18.6% ($1.02M to $1.21M), offsetting 14%
  of the decline. It is the only growing category month over month.
* **Year over year** August 2026 is -9.9% versus August 2025; Insulation is the fastest-growing
  category YoY (+52%).

## Story 2: margin compression with flat revenue (acceptance flow 2)

**Question:** "Revenue was roughly flat in Q2 2026 versus Q2 2025. Why did margin decline?"
"Margin" is deliberately ambiguous: the semantic model defines Gross Margin, Gross Margin %,
Contribution Margin and Operating Profit, and the glossary term "Margin" lists all four, so AnalystOS
must ask. The answer key uses Gross Margin %.

* Revenue $33.78M to $33.89M (**+0.30%**, flat).
* Gross margin % **26.34% to 21.93% (-4.41 pp)**; gross margin dollars $8.90M to $7.43M.
* Counterfactual attribution: unit-cost inflation **-4.04 pp** (Q2 2026 lines at the same product's
  cost 12 months earlier), discounting **-1.41 pp** (Q2 2026 lines at Q2 2025 segment discount
  rates), residual mix and other effects +1.04 pp.
* Cause 1: supplier cost increases in **Lumber (+10.0% unit cost)** and **Roofing (+8.6%)** from
  March 2026, not passed through to list prices. In Lumber it is mostly one supplier (Gulf States
  Forest Products, about +12% in March). Lumber GM% -7.9 pp, Roofing -7.2 pp, other categories about
  -1.5 to -2.4 pp (general inflation outpacing the 1.5% list-price increase).
* Cause 2: **Contractor discounting** up 3.0 pp (6.96% to 9.98%) from April 2026 to defend volume,
  deepest in Gulf Coast (+8 pp there, +2 pp elsewhere); Enterprise and Retail discount rates
  unchanged.

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

* Inventory value at month end: $13.19M (2025-06-30) to $17.21M (2026-06-30), **+30.5%**, while Q2
  revenue is flat year over year (+0.3%).
* **15 slow-moving SKUs** (the "Summit Select Signature" premium line in Plumbing, Electrical and
  Tools, launched 2025-09-15 and stocked in every branch) hold $3.18M at 2026-06-30 from zero a year
  earlier: **79% of the increase**. They sold 51, 89 and 103 units in Q4 2025, Q1 2026 and Q2 2026.
* They top the days-of-supply ranking (tens of thousands of days, or no sales at all in 90 days).
  Inventory Value is semi-additive: compare single snapshot dates, never sum months.

## Retired: Story 5 (forecast miss)

Earlier versions planted "Q2 2026 revenue 4.5% below the FY26 forecast" against a separate
`revenue_forecast` table. That table is replaced by the FY26 Operating Plan, against which Q2
revenue is on plan in total; the Contractor softness in Gulf Coast and Central Texas that drove the
old story now shows as the segment detail of Story 0.

## Verification

* `packages/demo-data/tests/test_stories.py` re-derives every magnitude above with independent
  DuckDB SQL over the raw files (not through `analystos_demo.measure`).
* `tests/evals/test_investigations.py` runs the investigator on each story and scores whether the
  expected evidence appears (see [analytical-correctness.md](analytical-correctness.md)).
