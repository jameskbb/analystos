/**
 * Semantic layer API: model, entities, dimensions, metrics (versioned), metric trees, glossary, calendar.
 * Paths and shapes follow apps/api/src/analystos_api/routers/semantic.py. Resources that the API wraps as
 * `{id, spec, updated_at}` are unwrapped into flat objects here so screens never see the envelope.
 */
import { api, request } from "../client";
import type * as T from "../types";
import { w, e, list, type List } from "./core";

/* ------------------------------------------------------------------ semantic-specific shapes */

export interface ModelIssue {
  severity: "error" | "warning";
  object: string;
  message: string;
}

export interface SemanticModelSpec {
  name?: string;
  version?: number;
  entities: (Omit<T.Entity, "id" | "table_name"> & { table: string })[];
  dimensions: Omit<T.Dimension, "id">[];
  metrics: (T.MetricDefinition & { id: string })[];
  relationships: {
    from_entity: string;
    from_col: string;
    to_entity: string;
    to_col: string;
    cardinality: string;
    approved: boolean;
    name?: string | null;
  }[];
  metric_trees: { root_metric: string; nodes: T.DriverEdge[]; name?: string | null; description?: string | null }[];
  glossary: Omit<T.GlossaryTerm, "id">[];
  calendar: T.CalendarConfig & { fiscal_year_naming?: string };
}

export interface SemanticModelOut {
  model: SemanticModelSpec;
  content_hash: string;
  issues: ModelIssue[];
  snapshot_id: string;
  snapshot_version: number;
  metric_versions: Record<string, { version_id?: string; version_no?: number; engine_version_id?: string }>;
}

export interface SemanticImportResult {
  counts: Record<string, number>;
  issues: ModelIssue[];
  snapshot_id: string;
}

export interface SemanticSnapshot {
  id: string;
  version_no: number;
  content_hash: string;
  reason: string;
  metric_version_ids: Record<string, unknown>;
  created_by?: string | null;
  created_at: string;
}

export interface MetricVersionEntry {
  version_id: string;
  version_no: number;
  definition: T.MetricDefinition & { id?: string };
  definition_hash: string;
  change_note: string;
  created_by?: string | null;
  created_at: string;
  is_current: boolean;
}

export interface MetricDiff {
  metric_id: string;
  from_version: number;
  to_version: number;
  changes: { field?: string; before?: unknown; after?: unknown; [k: string]: unknown }[];
}

export interface MetricExamples {
  metric_id: string;
  version_no: number;
  time_dimension: string | null;
  dimension: string | null;
  grain: string;
  points: { period: unknown; value: number | null }[];
  compiled: T.CompiledQuery & Record<string, unknown>;
  result: T.QueryResult;
}

export interface MetricInvestigationRef {
  id: string;
  title: string;
  status: string;
  created_at: string;
  metric_version: { version_id?: string; version_no?: number; engine_version_id?: string };
}

export interface TreeSuggestion {
  root_metric: string;
  edges: T.DriverEdge[];
  rationale: string[];
}

export interface PeriodResolution {
  input: string;
  window: T.TimeWindow;
  previous_period: T.TimeWindow;
  same_period_last_year: T.TimeWindow;
}

export interface DimensionValues {
  dimension: string;
  values: unknown[];
  counts: number[];
}

export type MetricInput = T.MetricDefinition & { id: string; change_note?: string };
export type EntityInput = { name: string; table: string; primary_key: string | string[]; grain_description?: string; label?: string | null; description?: string | null; default_time_dimension?: string | null };
export type DimensionInput = Omit<T.Dimension, "id">;
export type GlossaryInput = Omit<T.GlossaryTerm, "id" | "updated_at" | "metric_name">;

type Envelope<S> = { id: string; spec: S; updated_at: string; version_no?: number };

function unwrapEntity(x: Envelope<EntityInput>): T.Entity {
  const { table, ...rest } = x.spec;
  return { ...rest, id: x.id, table_name: table };
}
const unwrapDimension = (x: Envelope<DimensionInput>): T.Dimension => ({ ...x.spec, id: x.id });
const unwrapTree = (x: Envelope<Omit<T.MetricTree, "id">>): T.MetricTree & { version_no?: number } => ({
  ...x.spec,
  name: x.spec.name ?? undefined,
  description: x.spec.description ?? undefined,
  id: x.id,
  updated_at: x.updated_at,
  version_no: x.version_no,
});
const unwrapTerm = (x: Envelope<GlossaryInput>): T.GlossaryTerm => ({
  ...x.spec,
  id: x.id,
  metric_name: x.spec.metric_id ?? null,
  updated_at: x.updated_at,
});

/* ------------------------------------------------------------------ endpoints */

export const semantic = {
  model: (ws: string) => api.get<SemanticModelOut>(`${w(ws)}/semantic-models/current`),
  exportYaml: (ws: string) =>
    request<Response>(`${w(ws)}/semantic-models/current/yaml`, { raw: true, headers: { Accept: "text/plain" } }).then((r) =>
      r.text(),
    ),
  importYaml: (ws: string, yaml: string, changeNote = "Imported from YAML") =>
    api.put<SemanticImportResult>(`${w(ws)}/semantic-models/current/yaml`, { yaml, change_note: changeNote }),
  validate: (ws: string) => api.post<ModelIssue[]>(`${w(ws)}/semantic-models/current/validate`),
  history: (ws: string, limit = 50) => list(api.get<List<SemanticSnapshot>>(`${w(ws)}/semantic-models/history`, { limit })),
  snapshot: (ws: string, id: string) =>
    api.get<SemanticSnapshot & { model: Record<string, unknown> }>(`${w(ws)}/semantic-models/history/${e(id)}`),
  calendar: (ws: string) => api.get<T.CalendarConfig>(`${w(ws)}/semantic-models/calendar`),
  updateCalendar: (ws: string, body: T.CalendarConfig) => api.put<T.CalendarConfig>(`${w(ws)}/semantic-models/calendar`, body),
  resolvePeriod: (ws: string, text: string, today?: string) =>
    api.get<PeriodResolution>(`${w(ws)}/semantic-models/calendar/resolve`, { text, today }),
  entities: async (ws: string) =>
    (await list(api.get<List<Envelope<EntityInput>>>(`${w(ws)}/semantic-models/entities`))).map(unwrapEntity),
  upsertEntity: async (ws: string, body: EntityInput) =>
    unwrapEntity(await api.put<Envelope<EntityInput>>(`${w(ws)}/semantic-models/entities/${e(body.name)}`, body)),
  deleteEntity: (ws: string, name: string) => api.del(`${w(ws)}/semantic-models/entities/${e(name)}`),
};

export const metrics = {
  list: (ws: string, params: { include_archived?: boolean; tag?: string } = {}) =>
    list(api.get<List<T.Metric>>(`${w(ws)}/metrics`, params)),
  get: (ws: string, id: string) => api.get<T.Metric>(`${w(ws)}/metrics/${e(id)}`),
  create: (ws: string, body: MetricInput) => api.post<T.Metric>(`${w(ws)}/metrics`, body),
  /** Saves a new immutable version; omitted fields keep their current values. */
  update: (ws: string, id: string, body: Partial<T.MetricDefinition> & { change_note?: string }) =>
    api.put<T.Metric>(`${w(ws)}/metrics/${e(id)}`, body),
  archive: (ws: string, id: string) => api.del(`${w(ws)}/metrics/${e(id)}`),
  versions: (ws: string, id: string) => list(api.get<List<MetricVersionEntry>>(`${w(ws)}/metrics/${e(id)}/versions`)),
  diff: (ws: string, id: string, fromVersion: number, toVersion: number) =>
    api.get<MetricDiff>(`${w(ws)}/metrics/${e(id)}/diff`, { from_version: fromVersion, to_version: toVersion }),
  restore: (ws: string, id: string, versionNo: number) =>
    api.post<T.Metric>(`${w(ws)}/metrics/${e(id)}/versions/${versionNo}/restore`),
  lineage: (ws: string, id: string) => api.get<T.LineageGraph>(`${w(ws)}/metrics/${e(id)}/lineage`),
  examples: (ws: string, id: string, params: { dimension?: string; grain?: string; periods?: number } = {}) =>
    api.get<MetricExamples>(`${w(ws)}/metrics/${e(id)}/examples`, params),
  investigations: (ws: string, id: string) =>
    list(api.get<List<MetricInvestigationRef>>(`${w(ws)}/metrics/${e(id)}/investigations`)),
  /** Validates a full definition against the current model; returns issues (empty = valid). */
  validate: (ws: string, body: MetricInput) => api.post<ModelIssue[]>(`${w(ws)}/metrics/validate`, body),
};

export const dimensions = {
  list: async (ws: string) => (await list(api.get<List<Envelope<DimensionInput>>>(`${w(ws)}/dimensions`))).map(unwrapDimension),
  create: async (ws: string, body: DimensionInput) =>
    unwrapDimension(await api.post<Envelope<DimensionInput>>(`${w(ws)}/dimensions`, body)),
  /** Upserts the dimension named `name` (the body must carry the same name). */
  update: async (ws: string, name: string, body: DimensionInput) =>
    unwrapDimension(await api.put<Envelope<DimensionInput>>(`${w(ws)}/dimensions/${e(name)}`, body)),
  remove: (ws: string, name: string) => api.del(`${w(ws)}/dimensions/${e(name)}`),
  values: (ws: string, name: string, params: { q?: string; limit?: number } = {}) =>
    api.get<DimensionValues>(`${w(ws)}/dimensions/${e(name)}/values`, params),
};

export const metricTrees = {
  list: async (ws: string) =>
    (await list(api.get<List<Envelope<Omit<T.MetricTree, "id">>>>(`${w(ws)}/metric-trees`))).map(unwrapTree),
  get: async (ws: string, rootMetric: string) =>
    unwrapTree(await api.get<Envelope<Omit<T.MetricTree, "id">>>(`${w(ws)}/metric-trees/${e(rootMetric)}`)),
  /** Creates or replaces the tree rooted at `root_metric`. */
  save: async (ws: string, body: { root_metric: string; nodes: T.DriverEdge[]; name?: string | null; description?: string | null }) =>
    unwrapTree(await api.put<Envelope<Omit<T.MetricTree, "id">>>(`${w(ws)}/metric-trees/${e(body.root_metric)}`, body)),
  remove: (ws: string, rootMetric: string) => api.del(`${w(ws)}/metric-trees/${e(rootMetric)}`),
  suggest: (ws: string, rootMetric: string) =>
    api.post<TreeSuggestion>(`${w(ws)}/metric-trees/suggest`, { root_metric: rootMetric }),
  decideEdge: async (ws: string, rootMetric: string, body: { parent: string; child: string; decision: "approve" | "reject" }) =>
    unwrapTree(await api.post<Envelope<Omit<T.MetricTree, "id">>>(`${w(ws)}/metric-trees/${e(rootMetric)}/edges`, body)),
};

export const glossary = {
  list: async (ws: string) => (await list(api.get<List<Envelope<GlossaryInput>>>(`${w(ws)}/glossary`))).map(unwrapTerm),
  create: async (ws: string, body: GlossaryInput) => unwrapTerm(await api.post<Envelope<GlossaryInput>>(`${w(ws)}/glossary`, body)),
  update: async (ws: string, id: string, body: GlossaryInput) =>
    unwrapTerm(await api.put<Envelope<GlossaryInput>>(`${w(ws)}/glossary/${e(id)}`, body)),
  remove: (ws: string, id: string) => api.del(`${w(ws)}/glossary/${e(id)}`),
};
