/**
 * Advanced analyses (spec §39–44): forecasting, anomalies, segmentation, statistical tests,
 * correlation and regression. Paths and shapes follow apps/api/API.md "Analysis". Every call
 * persists an artifact, so results carry SQL, parameters, versions and filter context.
 */
import { api } from "../client";
import type * as T from "../types";
import { w, e, list, type List } from "./core";

export type AnalysisKind = "forecast" | "anomalies" | "segments" | "stats_test" | "correlation" | "regression";
export type Grain = "day" | "week" | "month" | "quarter";

export interface ApiFilter {
  dimension: string;
  op: string;
  values: unknown[];
}

/** A metric per period over the half-open window `[start, end)`. */
export interface TimeSeriesSource {
  metric_id: string;
  start: string;
  end: string;
  grain: Grain;
  time_dimension?: string | null;
  filters?: ApiFilter[];
}

export type TabularSource =
  | { sql: string; params?: Record<string, unknown> }
  | { saved_query_id: string; params?: Record<string, unknown> }
  | { metric_query: T.MetricQuery }
  | { table: string };

export interface Assumption {
  name: string;
  passed: boolean | null;
  detail: string;
}

export interface AnalysisOut<R = unknown> {
  id: string;
  kind: AnalysisKind;
  method: string;
  title: string;
  label: "Exploratory" | "Descriptive" | "Model estimate" | "Statistical test" | string;
  exploratory: boolean;
  summary: string;
  assumptions: Assumption[];
  caveats: string[];
  notes: string[];
  params: Record<string, unknown>;
  sql: string | null;
  input_row_count: number | null;
  input_truncated: boolean;
  metric_versions: Record<string, string>;
  dataset_versions: { table: string; content_hash: string; row_count: number }[];
  filter_context: T.FilterContextItem[];
  result: R;
  table: T.QueryResult | null;
  created_at: string;
}

/* ------------------------------------------------------------------ forecast */

export type ForecastModelName = "naive" | "seasonal_naive" | "ets" | "arima";

export interface ForecastPoint {
  timestamp: string;
  mean: number;
  lower: number;
  upper: number;
}

export interface BacktestFold {
  origin: string;
  horizon: number;
  mae: number;
  mape: number | null;
  coverage: number;
}

export interface ModelForecast {
  name: ForecastModelName;
  params: Record<string, unknown>;
  points: ForecastPoint[];
  backtest: { folds: BacktestFold[]; mae: number | null; mape: number | null; coverage: number | null } | null;
  aic: number | null;
  error: string | null;
  /** Why this model has no backtest (e.g. a seasonal model without two seasons of history). */
  backtest_note?: string | null;
}

export interface ForecastResult {
  history: { timestamp: string; value: number | null }[];
  horizon: number;
  frequency: string;
  seasonal_period: number | null;
  interval: number;
  models: ModelForecast[];
  best_model: ForecastModelName | null;
  selection_metric: string;
  notes: string[];
}

export interface ForecastBody extends TimeSeriesSource {
  horizon: number;
  models?: ForecastModelName[];
  interval: number;
  seasonal_period?: number | null;
  backtest_folds: number;
}

/* ------------------------------------------------------------------ anomalies */

export interface AnomalyPoint {
  timestamp: string;
  value: number | null;
  expected: number | null;
  lower: number | null;
  upper: number | null;
  score: number | null;
  is_anomaly: boolean;
  direction: "up" | "down" | null;
}

export interface ChangePoint {
  index: number;
  timestamp: string;
  mean_before: number;
  mean_after: number;
  magnitude: number;
  pct_change: number | null;
}

export interface AnomalyResult {
  points: AnomalyPoint[];
  anomalies: AnomalyPoint[];
  change_points: ChangePoint[];
  method: string;
  sensitivity: string;
  threshold: number;
  seasonal_period: number | null;
  window: number;
  frequency: string;
  notes: string[];
}

export type Sensitivity = "low" | "medium" | "high";

export interface AnomalyBody extends TimeSeriesSource {
  sensitivity: Sensitivity | number;
  seasonal_period?: number | null;
  min_relative_deviation?: number;
  change_points?: boolean;
}

export interface InvestigateAnomalyBody {
  metric_id: string;
  date: string;
  grain: "day" | "month";
  filters?: ApiFilter[];
  baseline?: "same_weekday_last_week" | "previous_day";
}

/* ------------------------------------------------------------------ segmentation */

export interface SegmentSummary {
  segment: string;
  count: number;
  share: number;
  value?: number | null;
  value_share?: number | null;
}

export interface RFMResult {
  as_of: string;
  customers: {
    customer: string;
    recency_days: number;
    frequency: number;
    monetary: number;
    r: number;
    f: number;
    m: number;
    segment: string;
  }[];
  segments: SegmentSummary[];
  quantiles: number;
  rules: [string, string][];
  notes: string[];
}

export interface QuadrantResult {
  rows: { item: string; growth: number | null; profitability: number | null; volume: number | null; quadrant: string }[];
  growth_threshold: number;
  profitability_threshold: number;
  segments: SegmentSummary[];
  notes: string[];
}

export interface ClusterResult {
  k: number;
  labels: number[];
  ids: unknown[] | null;
  centroids: Record<string, number>[];
  sizes: number[];
  silhouette: number | null;
  features: string[];
  exploratory: boolean;
  notes: string[];
}

export type SegmentsBody =
  | {
      method: "rfm";
      source?: TabularSource;
      table?: string;
      entity?: string;
      customer_col: string;
      date_col: string;
      amount_col: string;
      order_col?: string;
      as_of?: string;
      quantiles?: number;
    }
  | {
      method: "product_quadrants";
      dimension: string;
      growth_metric_id: string;
      profitability_metric_id: string;
      volume_metric_id?: string;
      current: { start: string; end: string };
      baseline?: { start: string; end: string };
      time_dimension?: string;
      filters?: ApiFilter[];
    }
  | { method: "kmeans"; source: TabularSource; features: string[]; id_column?: string; k?: number; standardize?: boolean };

/* ------------------------------------------------------------------ statistical tests */

export type StatTest = "t_test" | "chi_square" | "proportion" | "mean_ci" | "bootstrap_ci" | "proportion_ci";

export interface TestResult {
  test: string;
  statistic: number | null;
  p_value: number | null;
  df: number | null;
  alpha: number;
  significant: boolean | null;
  effect_size: number | null;
  effect_size_name: string | null;
  estimate: number | null;
  ci_low: number | null;
  ci_high: number | null;
  confidence: number;
  n: Record<string, number>;
  assumptions: Assumption[];
  interpretation: string;
  caveats: string[];
}

export interface ConfidenceInterval {
  estimate: number;
  low: number;
  high: number;
  confidence: number;
  method: string;
  n: number;
}

export interface StatsTestBody {
  test: StatTest;
  source: TabularSource;
  alpha?: number;
  confidence?: number;
  [field: string]: unknown;
}

/* ------------------------------------------------------------------ correlation / regression */

export interface CorrelationResult {
  method: "pearson" | "spearman";
  columns: string[];
  matrix: (number | null)[][];
  pairs: { a: string; b: string; r: number | null; p_value: number | null; n: number; strength: string }[];
  exploratory: boolean;
  caveat: string;
}

export interface RegressionResult {
  target: string;
  features: string[];
  coefficients: {
    name: string;
    coef: number;
    std_err: number | null;
    t: number | null;
    p_value: number | null;
    ci_low: number | null;
    ci_high: number | null;
  }[];
  r_squared: number | null;
  adj_r_squared: number | null;
  f_pvalue: number | null;
  n: number;
  vif: Record<string, number | null>;
  warnings: string[];
  exploratory: boolean;
  caveat: string;
}

export interface ImportanceResult {
  target: string;
  model: string;
  task: string;
  features: { name: string; importance_mean: number; importance_std: number }[];
  score_name: string;
  holdout_score: number | null;
  n: number;
  exploratory: boolean;
  caveat: string;
  notes: string[];
}

/* ------------------------------------------------------------------ endpoints */

const a = (ws: string) => `${w(ws)}/analysis`;

export const analysis = {
  forecast: (ws: string, body: ForecastBody) => api.post<AnalysisOut<ForecastResult>>(`${a(ws)}/forecast`, body),
  anomalies: (ws: string, body: AnomalyBody) => api.post<AnalysisOut<AnomalyResult>>(`${a(ws)}/anomalies`, body),
  investigateAnomaly: (ws: string, body: InvestigateAnomalyBody) =>
    api.post<T.Investigation>(`${a(ws)}/anomalies/investigate`, body),
  segments: (ws: string, body: SegmentsBody) =>
    api.post<AnalysisOut<RFMResult | QuadrantResult | ClusterResult>>(`${a(ws)}/segments`, body),
  statsTest: (ws: string, body: StatsTestBody) =>
    api.post<AnalysisOut<TestResult | ConfidenceInterval>>(`${a(ws)}/stats-test`, body),
  correlation: (ws: string, body: { source: TabularSource; columns?: string[]; method: "pearson" | "spearman" }) =>
    api.post<AnalysisOut<CorrelationResult>>(`${a(ws)}/correlation`, body),
  regression: (
    ws: string,
    body: { source: TabularSource; target: string; features: string[]; model: "ols" | "importance" },
  ) => api.post<AnalysisOut<RegressionResult | ImportanceResult>>(`${a(ws)}/regression`, body),
  list: (ws: string, params: { kind?: AnalysisKind; limit?: number } = {}) =>
    list(api.get<List<AnalysisOut>>(a(ws), params)),
  get: (ws: string, id: string) => api.get<AnalysisOut>(`${a(ws)}/${e(id)}`),
};
