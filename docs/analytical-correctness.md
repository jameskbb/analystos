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
| Operating margin by branch | three entities, two date columns |
| Actual and forecast by segment | actual vs plan on a conformed dimension |
| Budget by region from the Excel workbook | messy workbook ingested with explicit header rows |

Plus: every metric in the model compiles and returns a value for a month; grouping a customer count by
product category is refused with `GrainError`; an unapproved relationship is never used; `DELETE`,
`UPDATE`, `DROP`, stacked statements, `COPY` and `ATTACH` never reach the store.

## 3. Planted-story verification (`packages/demo-data/tests/`)

The demo generator plants five stories with target magnitudes. `test_stories.py` re-derives each one
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
lineage. `required` expectations fail the build; the others are scored. Run the scorecard with:

```bash
make evals            # or: uv run pytest tests/evals/test_investigations.py -s -k scorecard
```

`test_acceptance_api.py` runs acceptance flows 1 and 2 through the REST API (load the demo, schema,
relationships, metrics, investigation, SQL, charts, drill into Dallas, the major customer, finding,
lineage, report, rerun with an empty diff; and the literal §97 margin question), and `tests/e2e` runs
both flows in a browser.

## Latest scorecard

Run on 2026-09-18 (`uv run pytest tests/evals/test_investigations.py -s -k scorecard`):

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
[PASS] forecast_miss                4/4 evidence  Why was Q2 2026 revenue below forecast?
[PASS] forecast_miss_spec_phrasing  2/2 evidence  What caused the forecast miss?
[PASS] forecast_miss_q2             3/3 evidence  What caused the forecast miss in Q2 2026?
[PASS] conversion_decline           3/3 evidence  Why did conversion rate decline in Q2 2026?
[PASS] budget_comparison            2/2 evidence  How did August revenue compare to budget?
[PASS] segment_comparison           2/2 evidence  Compare Dallas vs Houston revenue in August
Evidence surfaced: 67/67 (100%); required: 67/67; questions passing: 16/16
```

Every expectation is now `required`, including the spec's literal wordings (the §97 margin question
with no period, "Which products grow fastest?", "Which inventory is unhealthy?", "What caused the
forecast miss?"), the region driver, the entry-tier mix shift, the unit-cost vs discounting split of
the margin decline, the semi-additive inventory value and budget and segment-versus-segment
comparisons.
