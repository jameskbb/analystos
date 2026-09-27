/**
 * API resource functions for the workbench: SQL workspace, explore/pivot, charts, Python, notebooks.
 * Paths and shapes follow apps/api/API.md ("SQL workspace", "Explore, pivot, charts, Python", "Notebooks").
 */
import { api, asList } from "../client";
import type * as T from "../types";
import { w, e, list, type List } from "./core";

/* ------------------------------------------------------------------ types local to the workbench */

export type ParamType = "string" | "number" | "integer" | "date" | "boolean" | "string_list";

/** A `$name` placeholder definition (API `QueryParameterDef`). */
export interface QueryParameterDef {
  name: string;
  type: ParamType;
  label?: string | null;
  default?: unknown;
  required?: boolean;
  options?: unknown[] | null;
}

/** Server-side chart suggestion (ECharts option included; the web renders its own option from the config). */
export interface ServerChartSuggestion {
  type: T.ChartType;
  title: string;
  reason: string;
  score: number;
  x?: string | null;
  y: string[];
  series?: string | null;
  option?: Record<string, unknown>;
}

export interface QueryRunOut {
  id: string;
  status: "succeeded" | "failed" | "rejected" | "running";
  origin: string;
  sql: string;
  params: Record<string, unknown>;
  saved_query_id?: string | null;
  data_source_id?: string | null;
  error?: string | null;
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
  columns: T.QueryColumn[];
  dataset_versions?: Record<string, unknown>;
  created_at: string;
}

export interface QueryRunResultOut extends QueryRunOut {
  result: T.QueryResult;
  warnings: string[];
  charts: ServerChartSuggestion[];
}

export interface QueryRunDetail extends QueryRunOut {
  result_snapshot: unknown[][];
}

export interface SavedQueryOut {
  id: string;
  name: string;
  description: string;
  sql: string;
  parameters: QueryParameterDef[];
  data_source_id?: string | null;
  tags: string[];
  chart?: Record<string, unknown> | null;
  version_no: number;
  created_at: string;
  updated_at: string;
}

export interface SavedQueryIn {
  name: string;
  description?: string;
  sql: string;
  parameters?: QueryParameterDef[];
  data_source_id?: string | null;
  tags?: string[];
  chart?: Record<string, unknown> | null;
}

export interface CompareResult {
  keys: string[];
  measures: string[];
  rows: Record<string, unknown>[];
  left_rows: number;
  right_rows: number;
  changed_rows: number;
  identical: boolean;
  left_run_id: string;
  right_run_id: string;
}

export interface ValidateSqlResult {
  ok: boolean;
  normalized_sql?: string | null;
  error?: string | null;
  code?: string | null;
  tables: string[];
  parameters: string[];
}

export interface SchemaTableOut {
  table: string;
  dataset_id?: string | null;
  dataset_name?: string | null;
  row_count?: number | null;
  columns: { name: string; type: string }[];
}

export interface WorkspaceSchema {
  tables: SchemaTableOut[];
  metrics: string[];
  dimensions: string[];
}

export type ExploreAggFn = "sum" | "count" | "count_distinct" | "avg" | "min" | "max" | "median";

export interface TableExploreBody {
  table: string;
  filters?: { column: string; op: string; values: unknown[] }[];
  group_by?: string[];
  aggregates?: { column: string | null; fn: ExploreAggFn | string; alias?: string }[];
  calculated?: { name: string; expr: string }[];
  order_by?: { field: string; desc: boolean }[];
  limit?: number;
}

export interface TableExploreResult {
  sql: string;
  result: T.QueryResult;
  filter_context: T.FilterContextItem[];
  charts?: ServerChartSuggestion[];
}

export interface MetricExploreResult {
  compiled: T.CompiledQuery;
  result: T.QueryResult;
  filter_context: T.FilterContextItem[];
  metric_versions?: Record<string, unknown>;
  charts?: ServerChartSuggestion[];
}

export interface GeneratedSql {
  sql: string;
  explanation?: string | null;
  tables?: string[];
  model?: string | null;
  /** Bounded repair loop (spec §89): each draft is validated read-only and dry-run with a small limit. */
  attempts?: { attempt: number; sql: string; ok: boolean; error?: string | null; row_count?: number | null; explanation?: string | null }[];
  repaired?: boolean;
  /** False when every attempt failed; the last draft is shown for editing but was not runnable. */
  executable?: boolean;
}

/* ------------------------------------------------------------------ queries */

export const queries = {
  run: (
    ws: string,
    body: {
      sql: string;
      params?: Record<string, unknown>;
      parameters?: QueryParameterDef[];
      limit?: number;
      data_source_id?: string | null;
      suggest_chart?: boolean;
    },
  ) => api.post<QueryRunResultOut>(`${w(ws)}/queries/run`, body),
  validate: (ws: string, sql: string) => api.post<ValidateSqlResult>(`${w(ws)}/queries/validate`, { sql }),
  historyPage: (
    ws: string,
    params: { limit?: number; offset?: number; origin?: string; status?: string; saved_query_id?: string; mine?: boolean } = {},
  ) => api.get<T.Page<QueryRunOut>>(`${w(ws)}/queries/history`, params),
  history: (ws: string, params: { limit?: number; offset?: number; origin?: string; status?: string } = {}) =>
    api.get<List<QueryRunOut>>(`${w(ws)}/queries/history`, params).then(asList),
  getRun: (ws: string, id: string) => api.get<QueryRunDetail>(`${w(ws)}/queries/history/${e(id)}`),
  rerun: (ws: string, id: string) => api.post<QueryRunResultOut>(`${w(ws)}/queries/history/${e(id)}/rerun`),
  compare: (ws: string, body: { left_run_id: string; right_run_id: string; keys?: string[] | null }) =>
    api.post<CompareResult>(`${w(ws)}/queries/compare`, body),
  saved: (ws: string) => list(api.get<List<SavedQueryOut>>(`${w(ws)}/queries/saved`)),
  getSaved: (ws: string, id: string) => api.get<SavedQueryOut>(`${w(ws)}/queries/saved/${e(id)}`),
  save: (ws: string, body: SavedQueryIn) => api.post<SavedQueryOut>(`${w(ws)}/queries/saved`, body),
  updateSaved: (ws: string, id: string, body: SavedQueryIn) => api.put<SavedQueryOut>(`${w(ws)}/queries/saved/${e(id)}`, body),
  deleteSaved: (ws: string, id: string) => api.del(`${w(ws)}/queries/saved/${e(id)}`),
  runSaved: (ws: string, id: string, body: { params?: Record<string, unknown>; limit?: number } = {}) =>
    api.post<QueryRunResultOut>(`${w(ws)}/queries/saved/${e(id)}/run`, body),
  schema: (ws: string) => api.get<WorkspaceSchema>(`${w(ws)}/queries/schema`),
  generate: (ws: string, prompt: string) => api.post<GeneratedSql>(`${w(ws)}/queries/generate`, { prompt }),
};

/* ------------------------------------------------------------------ explore / charts / python */

export const explore = {
  metrics: (ws: string, body: T.MetricQuery) => api.post<MetricExploreResult>(`${w(ws)}/explore/metrics`, body),
  compile: (ws: string, body: T.MetricQuery) => api.post<T.CompiledQuery>(`${w(ws)}/explore/compile`, body),
  table: (ws: string, body: TableExploreBody) => api.post<TableExploreResult>(`${w(ws)}/explore/table`, body),
};

export const python = {
  run: (ws: string, body: { code: string; inputs?: Record<string, string>; timeout_s?: number }) =>
    api.post<T.PythonResult>(`${w(ws)}/python/run`, body),
};

export const charts = {
  suggest: (ws: string, body: { columns: T.QueryColumn[]; rows?: unknown[][]; title?: string }) =>
    list(api.post<List<ServerChartSuggestion>>(`${w(ws)}/charts/suggest`, body)),
};

/* ------------------------------------------------------------------ notebooks */

export const notebooks = {
  list: (ws: string) => list(api.get<List<T.Notebook>>(`${w(ws)}/notebooks`)),
  get: (ws: string, id: string) => api.get<T.Notebook>(`${w(ws)}/notebooks/${e(id)}`),
  create: (ws: string, body: { title: string; description?: string }) => api.post<T.Notebook>(`${w(ws)}/notebooks`, body),
  update: (ws: string, id: string, body: { title?: string; description?: string }) =>
    api.patch<T.Notebook>(`${w(ws)}/notebooks/${e(id)}`, body),
  remove: (ws: string, id: string) => api.del(`${w(ws)}/notebooks/${e(id)}`),
  addCell: (ws: string, id: string, body: { kind: T.CellKind; source?: string; config?: Record<string, unknown>; position?: number }) =>
    api.post<T.NotebookCell>(`${w(ws)}/notebooks/${e(id)}/cells`, body),
  updateCell: (ws: string, id: string, cellId: string, body: Partial<Pick<T.NotebookCell, "source" | "config" | "kind">>) =>
    api.patch<T.NotebookCell>(`${w(ws)}/notebooks/${e(id)}/cells/${e(cellId)}`, body),
  deleteCell: (ws: string, id: string, cellId: string) => api.del(`${w(ws)}/notebooks/${e(id)}/cells/${e(cellId)}`),
  runCell: (ws: string, id: string, cellId: string) => api.post<T.NotebookCell>(`${w(ws)}/notebooks/${e(id)}/cells/${e(cellId)}/run`),
  runAll: (ws: string, id: string) => api.post<T.RunAllResult>(`${w(ws)}/notebooks/${e(id)}/run`),
  reorder: (ws: string, id: string, cellIds: string[]) =>
    api.post<T.Notebook>(`${w(ws)}/notebooks/${e(id)}/reorder`, { cell_ids: cellIds }),
  ipynbPath: (ws: string, id: string) => `${w(ws)}/notebooks/${e(id)}/ipynb`,
  fromInvestigation: (ws: string, investigationId: string) =>
    api.post<T.Notebook>(`${w(ws)}/investigations/${e(investigationId)}/notebook`),
};
