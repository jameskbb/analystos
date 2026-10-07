# Analytical correctness

Eloquence does not matter if the numbers are wrong. AnalystOS is tested for correctness at four
levels, and the tests need no network, Docker or API key.

## 1. Guarantees built into the engine

* **Grain safety.** The compiler aggregates each metric on its own entity and joins only parents, so a
  fact row is never repeated; child-grain filters become semi-joins; impossible groupings raise
  `GrainError` instead of double counting ([semantic-layer.md](semantic-layer.md)).
* **Approved joins only.** Suggested relationships are never used until approved.
* **Ratios are ratios of sums**, divided with `NULLIF(denominator, 0)`.
* **Half-open windows** `[start, end)` avoid off-by-one-day errors; time windows apply per metric to
  that metric's own date column.
* **No hidden filters.** Metric-definition filters (for example excluding cancelled orders) are
  listed in `filters_applied` next to query filters and shown as chips on every artifact.
* **Additivity is verified, not assumed.** Shares of a change are only claimed when segments sum to
  the total; overlapping segments get no share. Multiplicative decompositions use LMDI, whose effects
  sum exactly.
* **Gaps to plan reconcile.** Against a plan, every node of the metric tree is compared with its own
  governed plan metric (`plan_for`); the identity is checked on the actual and the plan side, the
  margin bridge's three effects add up to the margin gap by construction, and a reconciliation node
  sums every identity from executed numbers and says whether the parts add back to the gap (to within
  a cent).
* **Splits below plan grain stay exact and fair.** When a bridge effect is split by a dimension the plan
  does not carry (supplier), each member's plan rate is its own prior-year rate scaled by its plan cell's
  planned change, and the shift in member mix is its own node, so the parts add up to the segment's
  effect and a supplier that is always dearer is not booked as a cost increase.
* **The bridge is the tree.** The plan-to-actual waterfall is built from the reconciled parts of the
  tree, not computed separately, so its steps sum exactly to actual minus plan.
* **Correlation is not explanation.** Related signals (metrics the model declares operationally
  related) are measured in the segments where the explanation concentrates and reported as
  observations with relationship *not established*. They never enter a reconciled identity, the brief
  says they are not established as causes, and an LLM narrative that states one as a cause is
  rejected.
* **Validation on every test**: executed, non-empty, metric present, plausible row count, non-null,
  non-zero denominator, not truncated, grain, filters applied.
* **Reproducibility**: artifacts store SQL, parameters, filters, metric versions and dataset content
  hashes; reruns produce a diff.

## 2. SQL correctness evals (`tests/evals/test_sql_correctness.py`)

Each case compiles a `MetricQuery` with the engine, runs it on the demo workspace store, and compares
every cell with **SQL written by hand** over the raw files in a separate DuckDB connection:

| Case | What it catches |
|---|---|
| Revenue by month excludes cancelled orders | metric filter on a parent entity, month grain |
| August window is half-open | Aug 31 included, Sep 1 excluded |
| Revenue by branch | two-hop join order_lines -> orders -> branches |
| AOV by segment through duplicate customer rows | fan-out from a dirty dimension, ratio of sums |
| Revenue by region and category | conformed dimension plus a product join |
| Gross margin % by category for a quarter | derived and ratio metrics |
| Region IN and channel = filters | filters on two joined entities |
| Discount rate and ASP by tier | ratio of sums, not an average of line ratios |
| Orders by category | non-additive count distinct |
| Active customers, frequency, units per order by region | metrics from two grains pre-aggregated before they meet |
| Conversion rate by lead channel and by region | cohort metric, conformed region from leads |
| Inventory value and days of supply at one snapshot | semi-additive snapshot metric |
| Return rate | numerator dated by return date, denominator by order date |
| Operating profit by branch | three entities, two date columns |
| Actual and plan revenue by segment | actual vs plan on a conformed dimension |
| Operating profit vs plan by region | actual and plan operating profit from five tables and three date columns |
| Budget by region from the Excel workbook | messy workbook ingested with explicit header rows |

Plus: every metric in the model compiles and returns a value for a month; grouping a customer count by
product category is refused with `GrainError`; an unapproved relationship is never used; `DELETE`,
`UPDATE`, `DROP`, stacked statements, `COPY` and `ATTACH` never reach the store.

## 3. Planted-story verification (`packages/demo-data/tests/`)

The demo generator plants its stories (the flagship operating-profit miss against plan, the August
revenue decline, the Q2 margin compression, the conversion decline and the inventory build-up) with
target magnitudes. `test_stories.py` re-derives each one
with independent DuckDB SQL over the raw files (not through the generator's own measurement code),
for example August revenue -11.8% +/- 0.3 pp, Dallas as the largest branch contributor at roughly half
the decline, Lumber and Roofing margin erosion. `test_generator.py` checks determinism (same seed,
same content hash), scale, line arithmetic, the deliberate mess and that the stories hold for another
seed. See [demo-scenarios.md](demo-scenarios.md).

## 4. Investigation evals (`tests/evals/test_investigations.py`, `benchmark.yaml`)

The spec §83 benchmark runs through the real investigator. Each question lists expected evidence tied
to the answer key (`scenarios.json`): the headline change within tolerance, the top driver, specific
segments with minimum share or maximum rank, discount checks, ambiguity handling, the planted slow
SKUs, and **traceability**: every evidence node must reach executed SQL through its artifacts'
lineage. The flagship question ("We made our sales number last quarter, but we missed
our target for net profit. What happened?", with "net profit" resolved to Operating Profit through the glossary) is scored against the answer key's plan story: the premise verdict and
values, operating profit actual, plan and gap, the gross-margin and operating-expense parts, the
reconciliation, the unit-cost, discount and favorable mix effects, Lumber and Roofing in unit cost,
Contractor and Gulf Coast (inside Contractor) in discounting, the freight, overtime and payroll lines,
the overtime drill by branch, the related signal (values, segment, supporting response time, never a
reconciled component), no causal wording about it in the brief, a rerun with an empty diff, and an
operating review built from confirmed findings that reconciles only when every part is confirmed. `required` expectations fail the build; the others are scored. Run the scorecard with:

```bash
make evals            # or: uv run pytest tests/evals/test_investigations.py -s -k scorecard
```

`test_flagship_phrasings.py` asks the flagship the way executives do ("made our sales number", "top line
was on plan but the bottom line was way off", "nearly half of what we budgeted", "almost half short on
operating income", "short on profit", which stays ambiguous between gross margin and operating profit)
and requires the same reading every time: operating profit compared with its own plan, a met-plan premise
on revenue, the plan-variance template, and never a plan metric as the subject.

`test_phrasings.py` scores the interpreter alone against a 175-question corpus written blind to it
(`phrasings.yaml`: 118 dev questions to tune against, 57 holdout questions only ever reported). An
entry passes when every field it asserts matches (metric, or the candidates it must ask about, or
"not governed"; comparison; window and baseline; plan yardstick; premises; filters; unresolved filters;
breakdown; intent). Results, today = 2026-09-30:

| Split | Before the round of fixes | After |
|---|---:|---:|
| dev (118) | 58 (49.2%) | 114 (96.6%) |
| holdout (57) | 26 (45.6%) | 39 (68.4%) |

The eval requires dev >= 93.6% and holdout >= 63.4% (achieved minus 3 and 5 points). The dev/holdout
gap is large: the fixes generalise only partly, and unseen phrasings remain the main failure class.
Disputed expectations are listed in the orchestrator's notes rather than edited.

`test_acceptance_api.py` runs acceptance flows 1 and 2 through the REST API (load the demo, schema,
relationships, metrics, investigation, SQL, charts, drill into Dallas, the major customer, finding,
lineage, report, rerun with an empty diff; the literal §97 margin question; and the flagship: premise,
reconciled plan-variance tree, related signal kept apart, confirmation, an operating review from
confirmed findings, the Q2 business review carrying it and its plan KPIs, rerun with an empty diff),
and `tests/e2e` runs the flows in a browser.

## Latest scorecard

Run on 2026-09-29, round 2 data (`uv run pytest tests/evals/test_investigations.py -s -k scorecard`):

```
[PASS] revenue_decline              13/13 evidence  Why was August revenue down?
[PASS] region_driver                2/2 evidence  Which region drove the August revenue decline?
[PASS] customers_decline            1/1 evidence  Which customers contributed most to the August revenue decline?
[PASS] margin_flat_revenue          13/13 evidence  Revenue was roughly flat. Why did margin decline?
[PASS] margin_flat_revenue_explicit_period 6/6 evidence  Revenue was roughly flat in Q2 2026 versus Q2 2025. Why did margin decline?
[PASS] margin_explicit              6/6 evidence  Why did gross margin % decline in Q2 2026 vs Q2 2025?
[PASS] fastest_growing_spec_phrasing 2/2 evidence  Which products grow fastest?
[PASS] fastest_growing_categories   3/3 evidence  Which product categories grew revenue fastest in August 2026 compared with August 2025?
[PASS] unhealthy_inventory          3/3 evidence  Which inventory is unhealthy?
[PASS] inventory_q2_snapshot        2/2 evidence  Why did inventory value increase in Q2 2026 compared with Q2 2025?
[PASS] operating_profit_miss        34/34 evidence  We made our sales number last quarter, but we missed our target for net profit. What happened?
[PASS] operating_profit_miss_formal 4/4 evidence  We hit our revenue target last quarter, but operating profit missed plan badly. What happened?
[PASS] operating_profit_miss_plain  5/5 evidence  Why did operating profit miss plan in Q2 2026?
[PASS] operating_profit_gap_explains 3/3 evidence  Operating profit vs plan last quarter: what explains the gap?
[PASS] conversion_decline           3/3 evidence  Why did conversion rate decline in Q2 2026?
[PASS] budget_comparison            2/2 evidence  How did August revenue compare to budget?
[PASS] segment_comparison           2/2 evidence  Compare Dallas vs Houston revenue in August
Evidence surfaced: 104/104 (100%); required: 104/104; questions passing: 17/17
```

Every expectation is `required`, including the spec's literal wordings (the §97 margin question with
no period, "Which products grow fastest?", "Which inventory is unhealthy?"), the region driver, the
entry-tier mix shift, the unit-cost vs discounting split of the margin decline, the semi-additive
inventory value, budget and segment-versus-segment comparisons, and the flagship's 34 expectations
(including the plan-to-actual bridge steps, the supplier split with its mix remainder, Roofing left
unsplit, the fuel-surcharge and labor splits, the same-branch overtime and payroll offset, the grouped
other lines, strong evidence on operating expenses, no jargon in the brief or review, and the short
brief lead).
The retired forecast story ("What caused the forecast miss?") left with the FY26 forecast table: the
demo now carries one plan, the FY26 Operating Plan, and the flagship replaces it.
