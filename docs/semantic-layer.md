# Semantic layer

The semantic model is the single source of business definitions. The investigator, the Explorer,
dashboards and report KPIs all compile metrics through it, so "Revenue" means one thing everywhere.

Code: `packages/engine/src/analystos_engine/semantic/` (`models.py`, `compiler.py`, `joins.py`).
The Summit Supply model is a complete example:
`packages/demo-data/src/analystos_demo/data/semantic_model.yaml`.

## Objects

| Object | Fields | Notes |
|---|---|---|
| Entity | name, table, primary_key (one or more columns), grain_description, default_time_dimension | Declares the grain of a table. |
| Dimension | name, entity, expr, type (`categorical`, `time`, `numeric`), time_grains, label, synonyms | `expr` is SQL over the entity's own columns, e.g. `region` or `upper(trim(code))`. Time dimensions support `day, week, month, quarter, year, fiscal_quarter, fiscal_year` (`order_date__month`). |
| Metric | id, name, label, description, kind, expr/agg or numerator/denominator or formula, filters, format, owner, tags, version, canonical, synonyms, default_time_dimension, higher_is_better | See kinds below. |
| Relationship | from_entity.from_col -> to_entity.to_col, cardinality, **approved**, notes | Only approved, non many-to-many relationships are ever used for joins. |
| Metric tree | root_metric, nodes of DriverEdge(parent, child, relation, approved, suggested) | relation: `additive`, `subtractive`, `multiplicative`, `ratio_numerator`, `ratio_denominator`. |
| Glossary term | term, definition, formula, metric_id, candidate_metric_ids, related, synonyms | Used by question interpretation. |
| Calendar | fiscal_year_start_month, week_start, fiscal_year_naming | Drives "last quarter", "FY2026", "YTD" and so on. |

Metric kinds:

* **simple**: `agg(expr)` over the entity's rows (`sum`, `count`, `count_distinct`, `avg`, `min`,
  `max`), optionally with filters on any dimension. Revenue is
  `sum(net_amount)` on `order_lines` filtered by `order_status != cancelled`.
* **ratio**: `numerator / denominator`, both metric ids, always computed as a ratio of aggregates
  (never an average of row-level ratios) and guarded with `NULLIF(denominator, 0)`.
* **derived**: a formula over metric ids, e.g. `revenue - cogs`.

**Time aggregation.** A simple metric says how its values combine over time: `sum` (flows such as
revenue; the default), `last` or `first` (balances such as inventory: only the latest or earliest
snapshot date in each output period counts, so Q2 inventory value is the June 30 balance, never the
sum of three month-ends), or `avg` (the average over the period's dates of the per-date total).
Ratios and derived metrics inherit it through their inputs. **Maturity.** Cohort metrics can declare
`maturity_days` (the demo's `converted_leads` = 90): a query with `as_of` lists metrics whose period
is younger than that in `immature_metrics` and adds a warning, so a half-finished cohort is not
compared with a mature one.

The model round-trips through YAML (`to_yaml` / `from_yaml`), validates referential integrity
(`validate_model`, e.g. unknown dimensions, circular formulas, names shared by two metrics) and has a
content hash. A metric's `version_id` is `{id}@v{version}:{sha256 of the definition[:12]}`; the API
stores every definition change as a new immutable version and every model change as a snapshot.

## Ambiguity is a feature

A metric is only chosen silently when the question matches exactly one candidate or exactly one
canonical candidate. The demo glossary term **Margin** lists four candidates (Gross Margin,
Gross Margin %, Contribution Margin, Operating Margin) and no metric claims the bare word, so
"Why did margin decline?" returns `needs_disambiguation` with the four choices.

## Grain-safe compilation

`compile(model, MetricQuery)` turns metrics x dimensions x filters x time window into one SQL
statement that cannot double count:

1. Metrics are expanded into **atoms** (simple metrics). Ratios become ratio-of-sums, derived
   metrics substitute their formula.
2. Atoms are grouped by entity, metric filters and time dimension. Each group becomes a CTE that
   aggregates on its own entity's rows and only joins **parents** (many-to-one or one-to-one steps
   over approved relationships). Base rows are never repeated.
3. A filter on an entity that can only be reached through a one-to-many (child) path becomes an
   `EXISTS` semi-join, which never multiplies rows. Grouping by such a dimension is refused with
   `GrainError` and an explanation (for example, active customers counted on orders cannot be grouped
   by product category).
4. The time window `[start, end)` is a row-level predicate for each atom, on the query's time
   dimension when that is reachable, otherwise on the atom's own default time dimension. So
   `return_rate` filters returns by `return_date` and revenue by `order_date`, and
   `operating_margin` filters opex by `expense_month`.
5. Multiple CTEs meet on a spine of all dimension combinations using `IS NOT DISTINCT FROM` joins, so
   metrics from different grains are pre-aggregated before they meet.
6. Parent tables are joined through a copy de-duplicated on the join key, so a "one" side that is
   not actually unique in the data (the demo's customers file has duplicate rows) cannot multiply
   fact rows; `join_key_warnings` reports such keys, and approved relationships are re-measured on
   the data when they are approved.

The result records the grain, the join paths used, warnings, the metric versions and
`filters_applied`, human-readable labels for every filter including the ones inside metric
definitions (for example `order_status != cancelled (metric definition)`), so nothing filters
silently. The final SQL passes `ensure_read_only` and is pretty-printed for display.

## Conformed dimensions

To compare facts from different tables, attach them to a shared dimension entity. The demo models
`regions` and `customer_segments` as small reference entities that orders (through branches and
customers), leads, budgets and the revenue forecast all reach, so "revenue vs forecast by segment"
compiles in one query.

## Joins and relationship discovery

`relationships.discover` suggests joins from name similarity, type compatibility, target uniqueness
and value overlap (confidence high, medium or low; many-to-many is always low; matches that only work
after `trim()` are flagged). `analyze_join` measures observed cardinality, fan-out factor and orphan
rate. Suggestions are never used until an analyst approves them.
