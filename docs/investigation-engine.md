# Investigation engine

Code: `packages/investigator/src/analystos_investigator/`. Everything described here is deterministic
and works with no API key. The same data and definitions produce the same tree, with the same
content-derived node ids.

## Lifecycle

```
question -> interpret -> plan (+ hypotheses) -> [approval] -> execute -> tree -> brief answer + follow-ups
                 |                                                   |
                 +-- needs_disambiguation (choose a metric)          +-- drill, node actions, rerun -> diff
```

* `investigate(question, store, model, today)` interprets and plans. An ambiguous or unrecognised
  metric stops with status `needs_disambiguation`; `resolve_ambiguity(run, ..., {"margin":
  "gross_margin_pct"})` continues.
* Plans estimated above `approval_query_threshold` (40 queries) wait in `awaiting_approval`;
  lighter questions run immediately. Analysts can enable, disable, remove, add or edit steps
  (`add_step`, `remove_step`, `set_step_enabled`, `update_step`); the root measurement cannot be
  removed.
* `drill(run, node_id, dimension)` runs a new contribution analysis scoped to a node's segment.
* `rerun(run)` re-executes the frozen plan (absolute windows, filters, config) and replays drills,
  returning a diff by node id plus dataset-version and metric-version changes.
* Node actions: confirm, reject, needs review, annotate. A failed test cannot be confirmed.

## Interpretation

`interpret.py` resolves, in this order: comparison phrases (vs budget, vs forecast, year over year,
vs previous period), time expressions through the engine calendar (default "last month", with a
note), then a longest-match vocabulary over metric names and synonyms, glossary terms, dimensions and
dimension values. Dimension values become filters ("in Dallas"), negations become exclusions, and
unmatched exclusions are reported rather than dropped silently. In "Revenue was flat... why did margin
decline?" the metric attached to the change word is the subject and the others are context. Intents:
why_change, compare, breakdown, trend, lookup, forecast, anomaly.

## Planning and templates

Templates are strategies expressed with metric and dimension *roles*, not table names:
`revenue_decline`, `margin_variance`, `conversion_decline`, `churn`, `inventory_spike`,
`forecast_miss`, `regional_performance`, `general_change`. A plan contains:

* a root **compare** step (current vs baseline, or actual vs budget/forecast);
* a **decompose** step from the metric's tree (one tree per metric, never mixed; a multiplicative
  identity that misses its parent by more than 5% becomes a failed node);
* **contribution** steps for the dimensions reachable from the metric without fan-out, ranked by the
  template's roles (filter-pinned dimensions are skipped);
* **check** steps (for example discount rate, return rate), a **seasonality** check against the same
  change a year earlier, and **anomaly** or **trend** steps where relevant;
* dimensions named in the question ("by region") first;
* a **premise check** for each fact the question takes for granted ("revenue was roughly flat"):
  the premise is measured over the same periods and reported as holding or contradicted. When the
  question names no period, the interpreter picks the most recent comparable period in which the
  premise holds and lists the candidates it rejected;
* for margin metrics, a **margin bridge** (`decompose` with `method: margin_bridge`): current volumes
  at baseline unit costs isolate cost changes, current list-price sales at baseline discount rates
  isolate discounting, and the remainder is mix and list prices; the three add up to the change;
* for inventory health, a ranking of SKUs by days of supply and slow sales at the latest snapshot.

Ratio metrics (AOV, ASP, margin %) are split into segment effects only over segments that partition
the true totals; where segments overlap (one order spans several categories) the node shows
segment-level values with method `non_additive` and claims no share of the parent change.

`ExecutionConfig` defaults: top 5 segments per breakdown, drill depth 2, drill into the top 2
segments with share at least 20%, 6 to 8 dimensions (the template decides) and at most 300 queries,
10,000-row limit, 30 s timeout. A rerun keeps the analyst's decisions (confirmed, rejected, needs
review, annotations, saved findings) on matching nodes and flags a confirmed node for review when its
numbers moved materially.

## Hypotheses

Hypotheses are generated from the plan in categories: driver decomposition, dimension mix, customer
concentration, price and discount, cancellations and returns, seasonality. Ideas the data cannot test
(competitor pricing, weather) appear as nodes of type **hypothesis** with evidence
`hypothesis_only`, worded "Hypothesis (not tested): ...". They are never presented as findings.

## Execution, attribution and evidence

Every step compiles through the engine, runs read-only with a row cap and timeout, and is validated
(executed, metric present, non-empty, plausible row count, non-null value, non-zero denominator, not
truncated, grain, filters applied). A failure produces a failed node that says so and draws no
conclusion.

Each query becomes an **artifact** with SQL, parameters, filters, the window, metric versions, dataset
versions (table content hash and row count), a result snapshot, validation, warnings and an ECharts
chart spec. Derived results record `parent_ids`, so every node traces back to executed SQL.

Attribution (`attribution.py`):

* **Additive breakdowns** claim shares only when the segments are verified to sum to the total in both
  periods. Overlapping segments (for example distinct customers by channel) are reported without
  shares: "segments overlap, so no share of the total is claimed".
* **Ratio metrics** split into mix and rate effects.
* **Multiplicative trees** (Revenue = Orders x AOV) use LMDI, so effects sum exactly to the change;
  additive identities carry an explicit residual node when it exceeds 2%.

Segments are ranked by the absolute share of the parent change; the rest are grouped into an "other"
node. Dimensions are ranked by how disproportionate the change is relative to the baseline
distribution, and the top segments of the top dimensions are drilled.

**Evidence strength** (`evidence.py`, first rule that matches):

| Rule | Result |
|---|---|
| Not executed or failed | hypothesis only |
| Validation failed | weak |
| Attribution not exact, or identity residual above 2% | weak |
| Share of the parent change below 10% | weak |
| Tiny baseline with a huge percentage change | weak |
| Share at least 25% and corroborated by a second independent test | strong |
| Otherwise | moderate |

**Statement types**: *observation* (a measured fact), *supported explanation* (an exact attribution
of the parent's change), *hypothesis* (not tested). The UI renders them distinctly and shows the
reasons behind each strength; there is no numeric confidence score.

## Brief answer and follow-ups

The brief answer is assembled from the tree's own statements and numbers, for example: "Revenue
declined 11.8% ($11.37M to $10.02M, -$1.34M). The largest measurable driver was Orders (55% of the
change), particularly Branch = Dallas (49% of the change), where Customer = Trinity Ridge Construction
accounts for 53%." Follow-ups suggest unexplored dimensions, a year-over-year view, a budget
comparison when a budget metric exists, and the first untested hypothesis.

## Commands and summaries

`parse_command` turns text such as "break this down by region", "compare to last year", "exclude new
stores", "use gross revenue instead", "show the SQL", "save this as a finding", "build me a report" and
"turn this into a dashboard" into typed commands; the API executes them. `executive_summary` uses only
**confirmed** findings, separated into observations, supported explanations and hypotheses.

## Anomalies

`investigate_anomaly(store, model, metric_id, day)` compares a day with the same weekday a week
earlier (or the previous day), adds a robust z-score check (median/MAD over 56 trailing days, flagged
at |z| >= 3) and explains the day with the same tree machinery.
