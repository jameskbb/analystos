/** API resource functions (dashboards, reports, exports). Paths and shapes follow apps/api/API.md. */
import { api } from "../client";
import type * as T from "../types";
import { w, e, list, type List } from "./core";

/* ------------------------------------------------------------------ dashboard types */

/** A dashboard-level filter control; applied to every semantic (metric) tile. */
export interface DashboardFilter {
  dimension: string;
  op: string;
  values: unknown[];
  label?: string;
}

/** Either a calendar phrase resolved server-side with the fiscal calendar, or an explicit range. */
export interface DashboardDateRange {
  text?: string | null;
  start?: string | null;
  end?: string | null;
  dimension?: string | null;
}

/**
 * Tile binding. Semantic tiles carry a `metric_query` so the metric definition comes from the
 * semantic layer (never re-implemented per tile) and they inherit dashboard filters and dates.
 */
export interface TileBindingSpec {
  metric_query?: T.MetricQuery | null;
  /** KPI comparison baseline, resolved by the engine calendar. */
  compare?: "pop" | "yoy" | "none" | null;
  saved_query_id?: string | null;
  params?: Record<string, unknown> | null;
  artifact_id?: string | null;
  finding_id?: string | null;
}

export interface TileVizSpec {
  chart?: T.ChartConfig | null;
  format?: string | null;
  [k: string]: unknown;
}

export interface Tile {
  id: string;
  kind: T.TileKind;
  title: string;
  binding: TileBindingSpec;
  viz: TileVizSpec;
  text?: string;
}

export interface DashboardOut {
  id: string;
  name: string;
  description?: string;
  layout: T.LayoutItem[];
  filters: DashboardFilter[];
  date_range?: DashboardDateRange | null;
  tiles?: Tile[];
  tile_count?: number;
  version_no: number;
  created_at: string;
  updated_at?: string;
}

export interface TileDataOut {
  tile_id: string;
  kind?: T.TileKind;
  result?: T.QueryResult | null;
  kpi?: {
    value: number | null;
    baseline?: number | null;
    abs_change?: number | null;
    pct_change?: number | null;
    format?: string | null;
    label?: string | null;
  } | null;
  chart?: (T.ChartConfig & { option?: Record<string, unknown> | null }) | null;
  filter_context?: (T.FilterContextItem | T.FilterSpec)[];
  provenance?: {
    metric_versions?: Record<string, unknown>;
    sql?: string | null;
    dataset_versions?: Record<string, unknown>;
  } | null;
  compiled?: T.CompiledQuery | null;
  error?: string | null;
}

export type TileInput = Omit<Tile, "id">;

/* ------------------------------------------------------------------ report types */

export type BlockType =
  | "heading"
  | "narrative"
  | "finding"
  | "kpi"
  | "chart"
  | "table"
  | "methodology"
  | "sources"
  | "summary"
  | "open_questions";

export interface SourceItem {
  kind: string;
  label: string;
  ref_id?: string | null;
  version?: string | number | null;
}

export type SummaryEntry = string | { text: string; finding_id?: string | null; evidence_strength?: string | null };

export interface FindingSnapshot {
  statement: string;
  statement_type: string;
  evidence_strength: string;
  evidence_reasons?: string[];
  status?: string;
  filter_context?: (T.FilterContextItem | T.FilterSpec)[];
  values?: T.Finding["values"];
  metric_version_ids?: Record<string, unknown>;
  investigation_id?: string | null;
}

/**
 * A report block. `reviewed` / `excluded` drive the review-before-publish workflow; every other
 * field depends on `type` (see API.md "Reports").
 */
export interface ReportBlockSpec {
  id: string;
  type: BlockType | string;
  title?: string | null;
  text?: string | null;
  markdown?: string | null;
  finding_id?: string | null;
  snapshot?: FindingSnapshot | null;
  metric_id?: string | null;
  label?: string | null;
  period?: string | null;
  value?: number | null;
  baseline?: number | null;
  pct_change?: number | null;
  format?: string | null;
  sql?: string | null;
  option?: Record<string, unknown> | null;
  config?: T.ChartConfig | null;
  artifact_id?: string | null;
  provenance?: T.ChartProvenance | null;
  result?: T.QueryResult | null;
  items?: (SourceItem | string)[];
  observations?: SummaryEntry[];
  supported_explanations?: SummaryEntry[];
  hypotheses?: SummaryEntry[];
  reviewed?: boolean;
  excluded?: boolean;
  review_note?: string | null;
}

export interface ReportOut {
  id: string;
  title: string;
  kind: "custom" | "business_review" | "investigation" | string;
  status: "draft" | "in_review" | "published" | string;
  period?: string | null;
  investigation_id?: string | null;
  blocks: ReportBlockSpec[];
  version_no: number;
  published_at?: string | null;
  created_at: string;
  updated_at?: string | null;
}

/* ------------------------------------------------------------------ dashboards */

export const dashboards = {
  list: (ws: string) => list(api.get<List<DashboardOut>>(`${w(ws)}/dashboards`)),
  get: (ws: string, id: string) => api.get<DashboardOut>(`${w(ws)}/dashboards/${e(id)}`),
  create: (ws: string, body: { name: string; description?: string }) => api.post<DashboardOut>(`${w(ws)}/dashboards`, body),
  update: (
    ws: string,
    id: string,
    body: Partial<Pick<DashboardOut, "name" | "description" | "layout" | "filters" | "date_range">>,
  ) => api.patch<DashboardOut>(`${w(ws)}/dashboards/${e(id)}`, body),
  remove: (ws: string, id: string) => api.del(`${w(ws)}/dashboards/${e(id)}`),
  addTile: (ws: string, id: string, body: TileInput) => api.post<Tile>(`${w(ws)}/dashboards/${e(id)}/tiles`, body),
  updateTile: (ws: string, id: string, tileId: string, body: Partial<TileInput>) =>
    api.patch<Tile>(`${w(ws)}/dashboards/${e(id)}/tiles/${e(tileId)}`, body),
  removeTile: (ws: string, id: string, tileId: string) => api.del(`${w(ws)}/dashboards/${e(id)}/tiles/${e(tileId)}`),
  tileLineage: (ws: string, id: string, tileId: string) =>
    api.get<T.LineageGraph>(`${w(ws)}/dashboards/${e(id)}/tiles/${e(tileId)}/lineage`),
  tileData: (
    ws: string,
    id: string,
    tileId: string,
    body: { filters?: DashboardFilter[]; date_range?: DashboardDateRange | null } = {},
  ) => api.post<TileDataOut>(`${w(ws)}/dashboards/${e(id)}/tiles/${e(tileId)}/data`, body),
  versions: (ws: string, id: string) =>
    list(api.get<List<{ id: string; version_no: number; created_at: string }>>(`${w(ws)}/dashboards/${e(id)}/versions`)),
};

/* ------------------------------------------------------------------ reports */

export const reports = {
  list: (ws: string, params: { kind?: string } = {}) => list(api.get<List<ReportOut>>(`${w(ws)}/reports`, params)),
  get: (ws: string, id: string) => api.get<ReportOut>(`${w(ws)}/reports/${e(id)}`),
  create: (ws: string, body: { title: string; kind?: string; blocks?: ReportBlockSpec[]; investigation_id?: string | null }) =>
    api.post<ReportOut>(`${w(ws)}/reports`, body),
  update: (ws: string, id: string, body: Partial<Pick<ReportOut, "title" | "blocks">>) =>
    api.patch<ReportOut>(`${w(ws)}/reports/${e(id)}`, body),
  remove: (ws: string, id: string) => api.del(`${w(ws)}/reports/${e(id)}`),
  /** Builds a report from confirmed findings only (observations / supported explanations / hypotheses). */
  executiveSummary: (ws: string, body: { finding_ids?: string[]; investigation_id?: string | null; title?: string }) =>
    api.post<ReportOut>(`${w(ws)}/reports/executive-summary`, body),
  fromInvestigation: (ws: string, investigationId: string) =>
    api.post<ReportOut>(`${w(ws)}/reports/from-investigation`, { investigation_id: investigationId }),
  businessReview: (ws: string, body: { period: string; title?: string }) =>
    api.post<ReportOut | T.JobAccepted>(`${w(ws)}/reports/business-review`, body),
  submitForReview: (ws: string, id: string) => api.post<ReportOut>(`${w(ws)}/reports/${e(id)}/submit`),
  publish: (ws: string, id: string) => api.post<ReportOut>(`${w(ws)}/reports/${e(id)}/publish`),
  unpublish: (ws: string, id: string) => api.post<ReportOut>(`${w(ws)}/reports/${e(id)}/unpublish`),
};

/* ------------------------------------------------------------------ exports */

export type ExportTarget =
  | { kind: "query_run"; id: string }
  | { kind: "saved_query"; id: string }
  | { kind: "artifact"; id: string }
  | { kind: "report"; id: string }
  | { kind: "dashboard"; id: string }
  | { kind: "notebook"; id: string }
  | { kind: "investigation"; id: string }
  | { kind: "finding"; id: string }
  | { kind: "dataset"; id: string };

export const exportsApi = {
  path: (ws: string) => `${w(ws)}/exports`,
  create: (ws: string, body: { target: ExportTarget["kind"]; id: string; format: T.ExportFormat; options?: Record<string, unknown> }) =>
    api.post<T.ExportFile>(`${w(ws)}/exports`, body),
  list: (ws: string) => list(api.get<List<T.ExportFile>>(`${w(ws)}/exports`)),
  uploadImage: (ws: string, blob: Blob, filename: string, meta: Record<string, string> = {}) => {
    const form = new FormData();
    form.append("file", blob, filename);
    Object.entries(meta).forEach(([k, v]) => form.append(k, v));
    return api.upload<T.ExportFile>(`${w(ws)}/exports/images`, form);
  },
};
