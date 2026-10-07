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

Rules added from the blind phrasing corpus (all generic; nothing matches a question string):

* **Time.** "Q2 last year", "June this year", "from July to August" (August against July), "July vs
  August" (the later period is the one explained), "than we were at the start of the year" (a
  baseline), "year on year", "a year ago" and "than last year" (year over year). With several periods,
  the one the comparison is attached to ("in Q2 vs last year") is the window.
* **Metric choice.** When one word matches a metric and its parts ("freight", "conversion"), the parent
  in the metric tree wins; a parent named with its own parts is the subject ("leads not converting"),
  and alternatives within one identity ask about their parent ("orders or AOV?"). A plan metric is
  never the subject when an actual is named. A head word used as a verb ("it cost us") or a symbol
  ("%") does not name a metric. A generic word that is only background in another sentence does not
  block the question.
* **Governed vocabulary.** Glossary terms can declare a concept ambiguous (Margin, Profit, Margin Rate,
  Volume) or *not measured* (`not_measured: true`: net income after tax, days sales outstanding,
  customer satisfaction, on-time delivery, cost per lead, deal size, employee turnover). A question
  about an unmeasured concept is answered with "not governed" (`Interpretation.unmeasured_terms`),
  never with a metric that only shares a word with it, and no default metric is assumed.
* **Comparison against plan.** Besides "vs plan": "variance / bridge / gap to plan", "against the FY26
  Operating Plan", "ahead of / behind / over / under plan", "overrun", "blew through budget", a met or
  missed plan premise on the subject, and a plan metric named on its own ("quota attainment by rep":
  revenue against the revenue target).
* **Premises.** Over or under plan reads by what the metric is: a cost over plan missed it, under plan
  met it. The premise of a clause is said of the metric closest before the plan phrase. The subject's
  own stated direction ("why was August revenue *down*", "freight blew through budget") is recorded as a
  premise too (down or up, or missed or met against a plan); the plan never re-checks it. Clauses split
  on "when" and "so" as well.
* **Filters and breakdowns.** A value that belongs to several dimensions resolves to the one the
  question's metric can use (a category on sales, not the budget category). "By channel" on a lead
  metric means its lead channel; "by reason" finds the one usable dimension named that way. A proper
  name that matches no dimension value ("the Summit Select Signature line") is reported as an
  unresolved filter, not dropped.

## Planning and templates

Templates are strategies expressed with metric and dimension *roles*, not table names:
`revenue_decline`, `margin_variance`, `conversion_decline`, `churn`, `inventory_spike`,
`inventory_health`, `plan_variance`, `forecast_miss`, `regional_performance`, `general_change`.
`plan_variance` is chosen whenever a question compares a metric with its plan and every child of the
metric's tree has a governed plan counterpart (see [Comparison against plan](#comparison-against-plan)).
A plan contains:

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
  premise holds and lists the candidates it rejected. A premise about a plan ("we hit our revenue
  target", "revenue met plan", "margin fell short of budget") is measured against the plan metric it
  names, over the analysis window, with verdict *supported* or *contradicted* (meeting a plan allows a
  1% shortfall);
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

## Comparison against plan

A question that compares a metric with its plan ("operating profit missed plan", "vs budget") is
explained through the same governed identities as a change over time, node by node against plan.
Everything comes from the semantic model; nothing names a metric, table or story.

* **Pairing.** The root's plan metric is the one that declares `plan_for: <root>`; when several do
  (an operating plan, a budget, a sales quota) the question's word (plan, budget, target) decides. Each
  tree child is compared with its own `plan_for` counterpart; when a child has several, the one inside
  the root plan metric's dependency closure wins, so the whole tree reads from one plan. The pairs are
  frozen on the plan (`PlanContext.plan_metrics`) so a rerun compares with the same plan metrics.
* **Premise.** "We hit our revenue target" or "we made our sales number" becomes a premise on `revenue`
  measured against the plan metric the question names (a metric named like `revenue_target` finds its
  actual by `plan_for`, or by its name without the role word) or, when only a word such as "number",
  "plan" or "budget" names it, against revenue's own plan counterpart for that word. One premise per
  clause, and an actual metric wins over a bare plan word ("sales hit plan" is about sales). A metric
  stated as met, flat or up is context; the metric left to explain (or stated as short, missed or down)
  is the subject. A plan, budget or target metric is never the subject when an actual is named.
* **Plan as the yardstick.** Besides explicit wording ("vs plan", "below budget", "of what we budgeted"),
  a question compares against plan when it states a plan premise and the rest says short, under, below,
  behind or way off, or when it says the subject missed its target ("we missed our target for net
  profit"). The subject's baseline is always its own `plan_for` counterpart; the yardstick word only
  chooses among several (a "target" with no target-tagged counterpart falls back to the plan). Governed
  vocabulary does the rest: "top line" and "bottom line" are glossary synonyms, and "net profit" resolves
  to Operating Profit through the glossary term Net Profit (the demo has no interest or tax), shown in
  the interpretation notes.
* **Variance tree** (`decompose` with `method: plan_variance`). Additive and subtractive identities hold
  for actuals and plan alike, so each child's gap to its plan is an exact part of its parent's gap. The
  identity is checked on both sides (a 5% miss fails the step); any residual becomes its own node. The
  tree nests as deep as the model's identities with plan counterparts go (up to four levels: profit,
  operating expenses, labor, overtime), so freight splits into base freight and fuel surcharge and labor
  into payroll and overtime. Every node carries `root_effect`, its signed effect on the root, and a
  noun-phrase `title` ("Gross Margin shortfall", "Freight Cost overrun", "Payroll saving").
* **Materiality.** Within one identity, parts whose gap is below 5% of the parent's gap each
  (`MATERIALITY`, `variance.py`) are grouped into one "Other lines, net" node when there are at least
  two; it keeps their sum in the reconciliation and lists them (`TreeNode.members`). The same 5% keeps
  drill segments: smaller ones are folded into one "other" node.
* **Plan bridge** (`decompose` with `method: margin_bridge`, `mode: plan`). Where a node is exactly
  `revenue - cost`, its gap is split into unit cost, discounting and the rest, per plan cell: the
  conformed dimensions actuals and plan share (for the demo, category x segment x region). Actual
  volume at plan unit cost isolates unit-cost changes; actual list-price sales at the plan discount
  rate isolate discounting; the remainder is volume, mix and list price. When the model governs and
  plans unit cost as cost per list-price dollar (a ratio `cost / list-price revenue` with a plan
  counterpart), the cost basis is list-price sales instead of units, which is robust to unlike units in
  a cell. Each effect is split across the cell dimensions where it is most disproportionate to size, and
  drilled while the split stays informative (unit cost by category; discounting by segment, then by
  region inside the segment). Recorded unit costs and the discount rate against the same period a year
  earlier corroborate the direction (separate queries), for the effects and for the segments.
* **Below plan grain.** A bridge segment that still carries at least 20% of its effect and is not split
  further by a plan dimension (Lumber) is split by a dimension the plan does not carry (supplier, sales
  rep, product tier), exactly. The plan has no rate per supplier, so each member's plan rate in a plan
  cell is its own rate a year earlier scaled by the cell's planned change: P_rate_s = PY_rate_s x
  P_rate_cell / PY_rate_cell. The assumption, stated on the node (`TreeNode.method`): the plan's change of
  a cell's rate applies uniformly to its members. Costing every member at the cell's blended plan rate
  would book standing differences between members as cost changes. The shift in member mix inside cells
  is its own node, so the parts add up exactly to the segment's effect; a member with no sales a year
  earlier takes the cell's plan rate, and a dimension where less than half of the segment has a
  prior-year base is not used. Candidates have 2 to 25 values in the segment (`BELOW_GRAIN_MAX_SEGMENTS`);
  the one whose split is most disproportionate to size (total-variation distance at least 0.10) and
  whose largest member explains at least 20% is kept, ties going to the dimension with fewer values;
  otherwise nothing is split (Roofing, where both suppliers rose alike).
* **Variance drills.** Leaf lines of at least 20% of their parent's gap, offsets included (fuel surcharge,
  overtime, a payroll saving), are broken down by the conformed dimensions the metric and its plan share;
  the most concentrated breakdown is kept, and a spread-out gap says so. Siblings reuse the dimension
  chosen for the larger sibling when it is informative for them too, and a segment where one line hurt
  and its sibling helped names the offset and points at it (`TreeNode.offset_node_id`: overtime over plan
  in Austin, partly offset by a payroll saving in the same branch).
* **Reconciliation** (`reconcile` step, node with `role: reconciliation`). Every identity is summed from
  executed numbers: the children of each node, and the bridge effects, must reproduce the parent's
  `root_effect` to within a cent. The node lists the identities (`TreeNode.reconciliation`) and says
  *reconciled* or *not reconciled*.
* **Plan-to-actual bridge** (`Investigation.bridge`). Plan value, then the reconciled parts of each
  first-level part (the margin bridge effects, the expense lines, "Other lines, net" last), then the
  actual value, with running totals and a waterfall chart; the steps sum exactly to the gap.
* **Plain language.** Statements are written for an executive ("Higher unit costs cut $885.7k from Gross
  Margin: what was sold cost 3.5% more than the plan's cost rates"); the technical description (identity,
  counterfactual, rates held at plan) is `TreeNode.method`, shown in the calculation view. A segment
  leads with its own name ("Lumber: costs 7.4% above plan", "Austin: Overtime $141.2k over plan") and
  a breakdown reads "by product category" or "by supplier" (the dimension's label from the model, in
  lower-case words); the dimension is one level up in the tree and in the node's path. The operating
  review's watch list adds the path ("Lumber / Gulf States Forest Products: costs 12.1% above plan").
* **Related signals** (`related_signals` step). Metrics the model declares operationally related
  (`Metric.related_metrics`, for example conversion rate for revenue) are measured in each segment that
  explains at least 20% of its parent effect, over the analysis window and the previous period. A signal
  must move adversely, by at least 25%, and at least twice as much as the whole business; it is refined
  to the sub-segment that carries the move (a lead channel inside a region), and the metric's own related
  metrics are measured in the same segment (response time). Signals are tree nodes with
  `role: related_signal` and `relationship: not_established`, listed again in
  `Investigation.related_signals`; they are observations, never part of a reconciled identity, and every
  statement says the relationship to the gap is not established. A signal on a cohort metric whose
  `maturity_days` have not elapsed by the plan's reference date (the date the data is complete to,
  frozen at planning) is marked provisional and its evidence capped at weak; the brief says so.

The brief of a plan investigation leads with two or three sentences (`Investigation.brief_lead`), then
the reconciled long form (`brief_answer`). For the demo (seed 42):

> Operating Profit missed plan by $1.86M (-49.0%) in Q2 2026, while Revenue met Revenue Target. Gross
> Margin explains $1.32M of the gap and Operating Expenses $534.0k. The largest causes: unit-cost inflation
> (Lumber and Roofing; supplier Gulf States Forest Products), increased discounting (Contractor, Gulf
> Coast) and freight cost overrun (fuel surcharge).

The long form states the reconciled sentence ("Gross Margin shortfall accounts for $1.32M and Operating
Expenses overrun for $534.0k, which reconciles exactly to the $1.86M miss"), what sits inside each part
(Lumber, mostly supplier Gulf States Forest Products; Contractor, largest in Gulf Coast; the fuel
surcharge; overtime in Austin and San Antonio, partly offset by payroll savings in the same branches),
and the related signal as not established.

`variance_review(investigation, confirmed_ids)` builds the operating review from confirmed nodes only, as
a one-page memo: the headline and premise, the plan-to-actual bridge (table and waterfall), three to five
"what explains it" bullets that end in the measured operational cause, the related signals (not
established) and a measured watch list. Confirming a part accepts its drill-down; rejected nodes never
feed the review. Unconfirmed parts become one "Not yet confirmed" bridge step, so the waterfall still
closes, and are listed as pending; `complete` is true once the headline, the premise and every part are
confirmed. The reconciled wording is used only when every part of the top identity is confirmed.

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
of the parent's change), *hypothesis* (not tested). Related signals are observations with
`relationship: not_established`. The UI renders them distinctly and shows the reasons behind each
strength; there is no numeric confidence score.

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
**confirmed** findings, separated into observations, supported explanations, related signals
(relationship not established) and hypotheses.

## Anomalies

`investigate_anomaly(store, model, metric_id, day)` compares a day with the same weekday a week
earlier (or the previous day), adds a robust z-score check (median/MAD over 56 trailing days, flagged
at |z| >= 3) and explains the day with the same tree machinery.
