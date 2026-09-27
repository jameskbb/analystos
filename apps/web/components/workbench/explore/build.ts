/** Pure builders turning Explorer UI state into API requests. */
import type { MetricQuery } from "@/lib/api/types";
import type { ExploreAggFn, TableExploreBody } from "@/lib/api/resources/workbench";
import { partialAggregatesFor, type PivotValue } from "@/lib/pivot";
import { filterValues, type FilterDraft } from "./filter-editor";

export const ROWS_FIELD = "__rows__";

export interface AggDraft {
  fn: ExploreAggFn;
  column: string; // "" = count(*) rows
}

export interface TableConfig {
  table: string;
  filters: FilterDraft[];
  groupBy: string[];
  aggregates: AggDraft[];
  calculated: { name: string; expr: string }[];
  sort: { field: string; desc: boolean } | null;
  limit: number;
}

export interface PivotConfig {
  rows: string[];
  cols: string[];
  values: PivotValue[];
  subtotals: boolean;
  grandTotals: boolean;
  sortBy: "label" | "value";
  sortDir: "asc" | "desc";
}

export interface MetricConfig {
  metrics: string[];
  dimensions: { name: string; grain: string }[];
  filters: FilterDraft[];
  timeDimension: string;
  start: string;
  end: string;
  limit: number;
}

export function aggAlias(a: AggDraft): string {
  if (!a.column) return a.fn === "count" ? "row_count" : `${a.fn}_rows`;
  return `${a.fn}_${a.column}`.replace(/[^A-Za-z0-9_]/g, "_");
}

function apiFilters(filters: FilterDraft[], types: Record<string, string | undefined>) {
  return filters
    .map((f) => ({ f, values: filterValues(f, types[f.field]) }))
    .filter((x): x is { f: FilterDraft; values: unknown[] } => x.values !== null)
    .map(({ f, values }) => ({ column: f.field, op: f.op, values }));
}

export function buildTableRequest(cfg: TableConfig, types: Record<string, string | undefined>): TableExploreBody {
  const calculated = cfg.calculated.filter((c) => c.name.trim() && c.expr.trim()).map((c) => ({ name: c.name.trim(), expr: c.expr.trim() }));
  const aggregates =
    cfg.aggregates.length || !cfg.groupBy.length
      ? cfg.aggregates.map((a) => ({ column: a.column || null, fn: a.fn, alias: aggAlias(a) }))
      : [{ column: null, fn: "count", alias: "row_count" }];
  return {
    table: cfg.table,
    filters: apiFilters(cfg.filters, types),
    group_by: cfg.groupBy,
    aggregates,
    calculated,
    order_by: cfg.sort ? [{ field: cfg.sort.field, desc: cfg.sort.desc }] : [],
    limit: cfg.limit,
  };
}

/** Server groups at rows×columns grain and returns exact partial aggregates for `computePivot` ("partial" mode). */
export function buildPivotRequest(
  table: string,
  filters: FilterDraft[],
  calculated: TableConfig["calculated"],
  pivot: PivotConfig,
  types: Record<string, string | undefined>,
  limit = 50000,
): TableExploreBody {
  const aggs = partialAggregatesFor(pivot.values).map((a) => (a.column === ROWS_FIELD ? { ...a, column: null } : a));
  return {
    table,
    filters: apiFilters(filters, types),
    group_by: [...pivot.rows, ...pivot.cols.filter((c) => !pivot.rows.includes(c))],
    aggregates: aggs,
    calculated: calculated.filter((c) => c.name.trim() && c.expr.trim()),
    order_by: [],
    limit,
  };
}

/**
 * The API's time windows are half-open `[start, end)`. The Explorer's End picker is an inclusive
 * calendar date (what the user sees), so the request carries the following day.
 */
export function exclusiveEnd(inclusive: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(inclusive);
  if (!m) return inclusive;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + 1));
  return d.toISOString().slice(0, 10);
}

export function buildMetricQuery(cfg: MetricConfig): MetricQuery {
  return {
    metrics: cfg.metrics,
    dimensions: cfg.dimensions.map((d) => (d.grain ? `${d.name}__${d.grain}` : d.name)),
    filters: apiFilters(cfg.filters, {}).map((f) => ({ dimension: f.column, op: f.op, values: f.values })),
    time:
      cfg.start && cfg.end
        ? { dimension: cfg.timeDimension || null, start: cfg.start, end: exclusiveEnd(cfg.end) }
        : null,
    order_by: [],
    limit: cfg.limit,
  };
}

/** Current filters rendered for the FilterChips row (before the server echoes `filter_context`). */
export function draftChips(filters: FilterDraft[]): { dimension: string; op: string; values: unknown[] }[] {
  return filters
    .map((f) => ({ f, v: filterValues(f) }))
    .filter((x) => x.v !== null)
    .map(({ f, v }) => ({ dimension: f.field, op: f.op, values: v as unknown[] }));
}
