/**
 * API resource functions for the Data area: data sources, uploads/datasets, relationships and data quality.
 * Paths and shapes follow apps/api routers `datasets.py`, `data_sources.py`, `relationships.py` (+ quality).
 */
import { api } from "../client";
import type * as T from "../types";
import { w, e, list, type List } from "./core";

/* ------------------------------------------------------------------ types (engine/API shapes) */

export type InferredType = "integer" | "float" | "boolean" | "date" | "timestamp" | "string";

export interface DetectedColumn {
  name: string;
  original_name: string;
  inferred_type: InferredType | string;
  null_count?: number;
  non_null_count?: number;
  sample_values?: unknown[];
  notes?: string[];
  nonconforming_count?: number;
  nonconforming_examples?: string[];
  candidate_type?: string | null;
}

export interface SkippedRow {
  row: number;
  reason: "title" | "blank" | "total" | "note" | "malformed" | string;
  content?: string;
}

/** One table found in a file: the whole CSV, or one block of an Excel sheet. Rows are 1-based. */
export interface DetectedTable {
  key: string;
  sheet?: string | null;
  name_suggestion: string;
  header_row?: number | null;
  first_data_row: number;
  last_data_row: number;
  first_col?: number;
  last_col?: number;
  range?: string | null;
  columns: DetectedColumn[];
  row_count: number;
  preview_rows: unknown[][];
  skipped_rows?: SkippedRow[];
  notes?: string[];
  title?: string | null;
}

export interface SheetInfo {
  name: string;
  visible?: boolean;
  dimensions?: string | null;
  table_count?: number;
  empty?: boolean;
  max_row?: number;
  max_col?: number;
}

export interface CsvDialect {
  delimiter: string;
  quotechar?: string;
  encoding?: string;
  has_bom?: boolean;
}

export interface FileInspectionResult {
  path?: string;
  file_name: string;
  format: "csv" | "excel" | "parquet" | "json" | "ndjson" | string;
  size_bytes: number;
  encoding?: string | null;
  dialect?: CsvDialect | null;
  sheets: SheetInfo[];
  tables: DetectedTable[];
  warnings: string[];
}

export interface UploadRecord {
  id: string;
  workspace_id?: string;
  filename: string;
  size_bytes: number;
  sha256: string;
  file_kind: "csv" | "excel" | "parquet" | "json" | string;
  status: string;
  inspection: FileInspectionResult;
  created_at: string;
}

export interface PreviewOptions {
  sheet?: string | null;
  table_key?: string | null;
  header_row?: number | null;
  delimiter?: string | null;
  encoding?: string | null;
}

export interface FilePreviewResult {
  upload_id: string;
  options: PreviewOptions;
  preview: FileInspectionResult;
}

export interface IngestRequest {
  dataset_name?: string | null;
  table_name?: string | null;
  description?: string;
  if_exists?: "fail" | "replace" | "append";
  options: PreviewOptions & { column_types?: Record<string, InferredType>; column_names?: Record<string, string> };
}

export interface DatasetVersionRecord {
  id: string;
  dataset_id: string;
  version_no: number;
  content_hash: string;
  row_count: number;
  columns: { name: string; type: string; nullable?: boolean }[];
  ingest_options: Record<string, unknown>;
  upload_id?: string | null;
  captured_at: string;
}

export interface DatasetUsage {
  entities: string[];
  dimensions: string[];
  metrics: string[];
  quality_rules: string[];
  relationships: string[];
  saved_queries: string[];
  lineage: T.LineageGraph;
}

export interface ColumnStats {
  name: string;
  type: string;
  inferred_type?: string;
  row_count?: number;
  null_count: number;
  null_pct: number;
  distinct_count: number;
  distinct_pct?: number;
  min?: unknown;
  max?: unknown;
  mean?: number | null;
  median?: number | null;
  stddev?: number | null;
  quantiles?: Record<string, number | null>;
  top_values?: { value: unknown; count: number; pct?: number }[];
  min_length?: number | null;
  max_length?: number | null;
  zero_count?: number | null;
  negative_count?: number | null;
  semantic_roles?: string[];
  cardinality?: string;
}

export interface ProfileFinding {
  code: string;
  severity: "info" | "warning" | "error" | string;
  column?: string | null;
  message: string;
  count?: number | null;
  evidence?: Record<string, unknown>;
  sample_values?: unknown[];
}

export interface DatasetProfile {
  table: string;
  row_count: number;
  column_count: number;
  columns: ColumnStats[];
  issues: ProfileFinding[];
  duplicate_row_count?: number;
  sampled?: boolean;
  sample_size?: number | null;
  profiled_at?: string;
  elapsed_ms?: number;
}

/** JSON-schema driven connection form (engine `connector_config_schema`). */
export interface JsonSchemaProp {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  format?: string;
  enum?: unknown[];
  writeOnly?: boolean;
  anyOf?: JsonSchemaProp[];
}

export interface ConnectorKindSpec {
  kind: string;
  label: string;
  config_schema: { properties?: Record<string, JsonSchemaProp>; required?: string[]; title?: string; description?: string };
  secret_fields: string[];
}

export interface ConnectionTest {
  ok: boolean;
  message: string;
  latency_ms?: number | null;
  server_version?: string | null;
  read_only_enforced?: boolean;
  details?: Record<string, unknown>;
}

export interface RemoteSchema {
  name: string;
  table_count?: number | null;
}

export interface RemoteTable {
  name: string;
  schema_name: string;
  kind?: string;
  row_count?: number | null;
  columns: { name: string; type: string; nullable?: boolean }[];
}

export type Cardinality = "one_to_one" | "one_to_many" | "many_to_one" | "many_to_many";

export interface DataSourceInput {
  name: string;
  kind: string;
  description?: string;
  config: Record<string, unknown>;
  secrets: Record<string, unknown>;
}

/* ------------------------------------------------------------------ data sources */

export const dataSources = {
  kinds: () => list(api.get<List<ConnectorKindSpec>>("/data-sources/kinds")),
  list: (ws: string) => list(api.get<List<T.DataSource>>(`${w(ws)}/data-sources`)),
  get: (ws: string, id: string) => api.get<T.DataSource>(`${w(ws)}/data-sources/${e(id)}`),
  create: (ws: string, body: DataSourceInput) => api.post<T.DataSource>(`${w(ws)}/data-sources`, body),
  /** Tests unsaved settings; secrets are sent once and never stored by this call. */
  testSettings: (ws: string, body: DataSourceInput) => api.post<ConnectionTest>(`${w(ws)}/data-sources/test`, body),
  update: (
    ws: string,
    id: string,
    body: Partial<{ name: string; config: Record<string, unknown>; secrets: Record<string, unknown | null>; description: string }>,
  ) => api.patch<T.DataSource>(`${w(ws)}/data-sources/${e(id)}`, body),
  remove: (ws: string, id: string) => api.del(`${w(ws)}/data-sources/${e(id)}`),
  test: (ws: string, id: string) => api.post<ConnectionTest>(`${w(ws)}/data-sources/${e(id)}/test`),
  schemas: (ws: string, id: string) => list(api.get<List<RemoteSchema>>(`${w(ws)}/data-sources/${e(id)}/schemas`)),
  tables: (ws: string, id: string, schema: string) =>
    list(api.get<List<RemoteTable>>(`${w(ws)}/data-sources/${e(id)}/tables`, { schema })),
  previewTable: (ws: string, id: string, schema: string, table: string, limit = 100) =>
    api.get<T.QueryResult>(`${w(ws)}/data-sources/${e(id)}/tables/${e(schema)}/${e(table)}/preview`, { limit }),
  importTable: (
    ws: string,
    id: string,
    body: { schema_name: string; table: string; dataset_name?: string | null; table_name?: string | null; row_limit?: number | null },
  ) => api.post<T.JobAccepted>(`${w(ws)}/data-sources/${e(id)}/import`, body),
};

/* ------------------------------------------------------------------ datasets & uploads */

export const datasets = {
  list: (ws: string) => list(api.get<List<T.Dataset>>(`${w(ws)}/datasets`)),
  get: (ws: string, id: string) => api.get<T.Dataset>(`${w(ws)}/datasets/${e(id)}`),
  update: (ws: string, id: string, body: Partial<Pick<T.Dataset, "name" | "description" | "tags">>) =>
    api.patch<T.Dataset>(`${w(ws)}/datasets/${e(id)}`, body),
  remove: (ws: string, id: string) => api.del(`${w(ws)}/datasets/${e(id)}`),
  upload: (ws: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.upload<UploadRecord>(`${w(ws)}/datasets/uploads`, form);
  },
  uploads: (ws: string) => list(api.get<List<UploadRecord>>(`${w(ws)}/datasets/uploads`)),
  getUpload: (ws: string, uploadId: string) => api.get<UploadRecord>(`${w(ws)}/datasets/uploads/${e(uploadId)}`),
  deleteUpload: (ws: string, uploadId: string) => api.del(`${w(ws)}/datasets/uploads/${e(uploadId)}`),
  /** Re-parses the stored file with explicit options (sheet, header row, delimiter) without ingesting. */
  previewUpload: (ws: string, uploadId: string, options: PreviewOptions, limit = 50) =>
    api.post<FilePreviewResult>(`${w(ws)}/datasets/uploads/${e(uploadId)}/inspect`, { options, limit }),
  ingest: (ws: string, uploadId: string, body: IngestRequest) =>
    api.post<T.JobAccepted>(`${w(ws)}/datasets/uploads/${e(uploadId)}/ingest`, body),
  preview: (ws: string, id: string, params: { limit?: number; offset?: number; order_by?: string; direction?: "asc" | "desc" } = {}) =>
    api.get<T.QueryResult>(`${w(ws)}/datasets/${e(id)}/preview`, params),
  profile: (ws: string, id: string) => api.get<DatasetProfile>(`${w(ws)}/datasets/${e(id)}/profile`),
  reprofile: (ws: string, id: string) => api.post<T.JobAccepted>(`${w(ws)}/datasets/${e(id)}/profile`),
  versions: (ws: string, id: string) => list(api.get<List<DatasetVersionRecord>>(`${w(ws)}/datasets/${e(id)}/versions`)),
  usage: (ws: string, id: string) => api.get<DatasetUsage>(`${w(ws)}/datasets/${e(id)}/usage`),
  lineage: (ws: string, id: string) => api.get<T.LineageGraph>(`${w(ws)}/datasets/${e(id)}/lineage`),
  /** Ids of metrics computed from this dataset. */
  metrics: (ws: string, id: string) => list(api.get<List<string>>(`${w(ws)}/datasets/${e(id)}/metrics`)),
};

/* ------------------------------------------------------------------ relationships */

export const relationships = {
  list: (ws: string, params: { status?: "suggested" | "approved" | "rejected"; table?: string } = {}) =>
    list(api.get<List<T.Relationship>>(`${w(ws)}/relationships`, params)),
  discover: (ws: string) => api.post<T.JobAccepted>(`${w(ws)}/relationships/discover`),
  create: (
    ws: string,
    body: { from_table: string; from_col: string; to_table: string; to_col: string; cardinality: Cardinality; approve?: boolean },
  ) => api.post<T.Relationship>(`${w(ws)}/relationships`, body),
  approve: (ws: string, id: string, body: { cardinality?: Cardinality; note?: string } = {}) =>
    api.post<T.Relationship>(`${w(ws)}/relationships/${e(id)}/approve`, body),
  reject: (ws: string, id: string, body: { note?: string } = {}) =>
    api.post<T.Relationship>(`${w(ws)}/relationships/${e(id)}/reject`, body),
  update: (ws: string, id: string, body: { cardinality?: Cardinality; note?: string }) =>
    api.patch<T.Relationship>(`${w(ws)}/relationships/${e(id)}`, body),
  /** Re-runs join analysis (observed cardinality, fan-out, orphans) and returns the updated relationship. */
  analyze: (ws: string, id: string) => api.post<T.Relationship>(`${w(ws)}/relationships/${e(id)}/analyze`),
  remove: (ws: string, id: string) => api.del(`${w(ws)}/relationships/${e(id)}`),
};

/* ------------------------------------------------------------------ quality */

export const quality = {
  rules: (ws: string, params: { dataset_id?: string; table?: string; status?: string } = {}) =>
    list(api.get<List<T.QualityRule>>(`${w(ws)}/quality/rules`, params)),
  createRule: (
    ws: string,
    body: {
      table_name: string;
      dataset_id?: string | null;
      name: string;
      kind: string;
      column?: string | null;
      params: Record<string, unknown>;
      severity: string;
      description?: string;
    },
  ) => api.post<T.QualityRule>(`${w(ws)}/quality/rules`, body),
  updateRule: (ws: string, id: string, body: Partial<Pick<T.QualityRule, "name" | "params" | "severity" | "status" | "description">>) =>
    api.patch<T.QualityRule>(`${w(ws)}/quality/rules/${e(id)}`, body),
  deleteRule: (ws: string, id: string) => api.del(`${w(ws)}/quality/rules/${e(id)}`),
  suggest: (ws: string, datasetId: string) =>
    list(api.post<List<T.QualityRule>>(`${w(ws)}/quality/suggest`, { dataset_id: datasetId })),
  acceptSuggestion: (ws: string, id: string) => api.post<T.QualityRule>(`${w(ws)}/quality/rules/${e(id)}/accept`),
  runRule: (ws: string, id: string) => api.post<T.QualityRun>(`${w(ws)}/quality/rules/${e(id)}/run`),
  runAll: (ws: string, body: { dataset_id?: string } = {}) =>
    api.post<List<T.QualityRun> | T.JobAccepted>(`${w(ws)}/quality/run`, body),
  runs: (ws: string, params: { rule_id?: string; dataset_id?: string; limit?: number } = {}) =>
    list(api.get<List<T.QualityRun>>(`${w(ws)}/quality/runs`, params)),
  run: (ws: string, runId: string) => api.get<T.QualityRun>(`${w(ws)}/quality/runs/${e(runId)}`),
  failingRows: (ws: string, runId: string, params: { limit?: number } = {}) =>
    api.get<T.QueryResult>(`${w(ws)}/quality/runs/${e(runId)}/failing-rows`, params),
};
