/**
 * Typed shapes for the AnalystOS REST API (`/api/v1`).
 * Mirrors the API's Pydantic schemas (see apps/api/API.md and the OpenAPI document).
 * Fields that the API may omit are optional so the UI degrades gracefully.
 */

export type ID = string;
export type ISODate = string;

/* ------------------------------------------------------------------ auth / workspace */

export interface User {
  id: ID;
  email: string;
  name: string;
  is_local?: boolean;
  created_at?: ISODate;
  last_login_at?: ISODate | null;
}

export interface SessionInfo {
  authenticated: boolean;
  auth_mode: "local" | "password";
  user: User | null;
  csrf_token?: string | null;
  signup_allowed: boolean;
  default_workspace_id?: ID | null;
  /** Local (no-password) mode is on but this request is not from the server's own machine: sign in instead. */
  local_mode_denied?: boolean;
  /** Creating the first account needs the server's AOS_BOOTSTRAP_TOKEN. */
  bootstrap_token_required?: boolean;
}

export type Role = "owner" | "editor" | "viewer";

export interface Workspace {
  id: ID;
  name: string;
  description?: string;
  settings?: WorkspaceSettings;
  role?: Role;
  created_at?: ISODate;
  updated_at?: ISODate;
  counts?: Partial<Record<"datasets" | "metrics" | "investigations" | "findings" | "dashboards" | "reports", number>>;
}

export interface WorkspaceSettings {
  fiscal_year_start_month?: number;
  week_start?: "monday" | "sunday" | string;
  default_currency?: string;
  timezone?: string;
  [key: string]: unknown;
}

export interface Member {
  user_id: ID;
  email: string;
  name: string;
  role: Role;
  created_at?: ISODate;
}

export interface ApiToken {
  id: ID;
  name: string;
  prefix: string;
  workspace_id?: ID | null;
  read_only: boolean;
  created_at: ISODate;
  last_used_at?: ISODate | null;
  expires_at?: ISODate | null;
  revoked_at?: ISODate | null;
}

export interface ApiTokenCreated {
  token: string;
  meta: ApiToken;
}

export interface Job {
  id: ID;
  workspace_id?: ID | null;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed";
  progress: number;
  message: string;
  params?: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  error?: string | null;
  resource_type?: string | null;
  resource_id?: ID | null;
  created_at: ISODate;
  started_at?: ISODate | null;
  finished_at?: ISODate | null;
}

export interface JobAccepted {
  job: Job;
  poll_url: string;
}

/* ------------------------------------------------------------------ data */

export type ConnectorKind = "files" | "duckdb" | "postgres" | "mysql" | "sqlserver" | "snowflake" | "bigquery";

export interface DataSource {
  id: ID;
  name: string;
  kind: ConnectorKind | string;
  description?: string;
  config: Record<string, unknown>;
  secret_fields?: string[];
  has_secrets?: boolean;
  status: string;
  last_tested_at?: ISODate | null;
  last_test_message?: string | null;
  created_at?: ISODate;
}

export interface ConnectorField {
  name: string;
  label: string;
  type: "string" | "integer" | "boolean" | "password" | "text" | string;
  required?: boolean;
  secret?: boolean;
  default?: unknown;
  help?: string;
}

export interface ConnectorKindInfo {
  kind: string;
  label: string;
  fields: ConnectorField[];
  driver_available?: boolean;
  notes?: string;
}

export interface ConnectionTestResult {
  ok: boolean;
  message: string;
  latency_ms?: number | null;
  details?: Record<string, unknown>;
}

export interface ColumnInfo {
  name: string;
  type: string;
  nullable?: boolean;
  description?: string;
  semantic_role?: string | null;
  ordinal?: number;
}

export interface Dataset {
  id: ID;
  name: string;
  table_name: string;
  description?: string;
  source_kind: "upload" | "connector" | "demo" | string;
  source_ref?: Record<string, unknown>;
  data_source_id?: ID | null;
  row_count: number;
  columns: ColumnInfo[];
  profile_status: "pending" | "running" | "ready" | "failed" | string;
  profiled_at?: ISODate | null;
  current_version_id?: ID | null;
  current_version?: DatasetVersion | null;
  tags?: string[];
  issue_count?: number;
  quality?: { rules: number; failing: number; last_run_at?: ISODate | null } | null;
  created_at: ISODate;
  updated_at?: ISODate;
}

export interface DatasetVersion {
  id: ID;
  version_no: number;
  content_hash: string;
  row_count: number;
  columns?: ColumnInfo[];
  ingest_options?: Record<string, unknown>;
  upload_id?: ID | null;
  captured_at: ISODate;
}

export interface QueryColumn {
  name: string;
  type: string;
}

export interface QueryResult {
  columns: QueryColumn[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
  sql?: string;
}

export interface ColumnProfile {
  name: string;
  type: string;
  inferred_type?: string;
  semantic_role?: string | null;
  role_guesses?: string[];
  null_count: number;
  null_pct: number;
  distinct_count: number;
  distinct_pct?: number;
  min?: unknown;
  max?: unknown;
  mean?: number | null;
  median?: number | null;
  std?: number | null;
  quantiles?: Record<string, number | null>;
  top_values?: { value: unknown; count: number; pct?: number }[];
  histogram?: { bins: number[]; counts: number[] } | { edges: number[]; counts: number[] } | null;
  cardinality?: "constant" | "low" | "medium" | "high" | "unique" | string;
  issues?: ProfileIssue[];
}

export interface ProfileIssue {
  kind: string;
  severity: "info" | "warning" | "error" | string;
  column?: string | null;
  message: string;
  evidence?: Record<string, unknown> | string | null;
}

export interface TableProfile {
  table: string;
  row_count: number;
  column_count?: number;
  duplicate_row_count?: number;
  columns: ColumnProfile[];
  issues: ProfileIssue[];
  profiled_at?: ISODate;
}

export interface ExcelSheetInspection {
  name: string;
  header_row?: number | null;
  data_start_row?: number | null;
  n_rows?: number;
  n_cols?: number;
  empty_rows?: number[];
  empty_cols?: number[];
  title_rows?: number[];
  merged_title?: string | null;
  table_blocks?: { start_row: number; end_row: number; start_col: number; end_col: number; header_row?: number }[];
  columns?: ColumnInfo[];
  preview?: { columns: string[]; rows: unknown[][] };
  raw_preview?: unknown[][];
  warnings?: string[];
}

export interface FileInspection {
  file_kind: "csv" | "excel" | "parquet" | "json" | string;
  filename?: string;
  encoding?: string | null;
  delimiter?: string | null;
  has_header?: boolean;
  row_estimate?: number | null;
  columns?: ColumnInfo[];
  preview?: { columns: string[]; rows: unknown[][] };
  sheets?: ExcelSheetInspection[];
  warnings?: string[];
}

export interface Upload {
  id: ID;
  filename: string;
  size_bytes: number;
  file_kind: string;
  status: string;
  inspection: FileInspection;
  created_at: ISODate;
}

export interface IngestOptions {
  table_name?: string;
  dataset_name?: string;
  sheet?: string;
  header_row?: number | null;
  data_start_row?: number | null;
  skip_empty_rows?: boolean;
  block_index?: number | null;
  delimiter?: string | null;
  encoding?: string | null;
  replace?: boolean;
}

export type Confidence = "high" | "medium" | "low";

export interface Relationship {
  id: ID;
  from_table: string;
  from_col: string;
  to_table: string;
  to_col: string;
  cardinality: string;
  confidence: Confidence | string;
  signals: string[];
  overlap_pct?: number | null;
  status: "suggested" | "approved" | "rejected" | string;
  origin?: "discovered" | "manual" | string;
  join_analysis?: JoinAnalysis | null;
  decided_at?: ISODate | null;
  created_at?: ISODate;
}

export interface JoinAnalysis {
  observed_cardinality: string;
  fanout_factor?: number | null;
  orphan_pct?: number | null;
  warnings?: string[];
}

export type QualityRuleKind =
  | "not_null"
  | "unique"
  | "range"
  | "between"
  | "fk_exists"
  | "not_future"
  | "allowed_values"
  | "regex"
  | "custom_sql";

export interface QualityRule {
  id: ID;
  dataset_id?: ID | null;
  table_name: string;
  name: string;
  kind: QualityRuleKind | string;
  column?: string | null;
  params: Record<string, unknown>;
  severity: "info" | "warning" | "error" | string;
  origin: "manual" | "suggested" | "demo" | string;
  status: "active" | "suggested" | "disabled" | string;
  description?: string;
  last_run_at?: ISODate | null;
  last_passed?: boolean | null;
  last_failing_count?: number | null;
  created_at?: ISODate;
}

export interface QualityRun {
  id: ID;
  rule_id: ID;
  rule_name?: string;
  table_name?: string;
  passed: boolean;
  /** "error" = the rule could not run; never a data failure (no failing rows). */
  status?: "passed" | "failed" | "error";
  failing_count: number;
  total_count?: number | null;
  sample_failing_rows?: { columns: string[]; rows: unknown[][] } | QueryResult | Record<string, unknown>;
  sql: string;
  suggested_fix?: string | null;
  error?: string | null;
  duration_ms?: number;
  created_at: ISODate;
}

/* ------------------------------------------------------------------ semantic */

export type MetricFormat = "currency" | "number" | "percent" | "integer";

export interface MetricDefinition {
  name: string;
  label: string;
  description?: string;
  kind: "simple" | "ratio" | "derived" | string;
  expr?: string | null;
  entity?: string | null;
  agg?: "sum" | "count" | "count_distinct" | "avg" | "min" | "max" | string | null;
  numerator?: string | null;
  denominator?: string | null;
  formula?: string | null;
  filters?: unknown[];
  format: MetricFormat | string;
  owner?: string | null;
  tags?: string[];
  canonical?: boolean;
  synonyms?: string[];
  time_dimension?: string | null;
  default_time_dimension?: string | null;
  higher_is_better?: boolean;
  /**
   * How values combine over time (engine `Metric.time_aggregation`): "sum" for flows; "last"/"first" for
   * balances such as inventory (semi-additive: only the latest/earliest snapshot in each period);
   * "avg" for the average of per-date totals.
   */
  time_aggregation?: TimeAggregation;
}

export type TimeAggregation = "sum" | "last" | "first" | "avg";

export interface Metric extends MetricDefinition {
  id: ID;
  version: number;
  /** Immutable version metadata returned by the API (`version` equals `version_no`). */
  version_no?: number;
  version_id?: ID;
  record_id?: ID;
  engine_version_id?: string;
  change_note?: string;
  current_version_id?: ID | null;
  archived?: boolean;
  created_at?: ISODate;
  updated_at?: ISODate;
  dimensions?: string[];
  datasets?: string[];
}

export interface MetricVersion {
  id: ID;
  metric_id: ID;
  version_no: number;
  definition: MetricDefinition;
  definition_hash: string;
  change_note?: string;
  created_by?: string | null;
  created_at: ISODate;
}

export interface Dimension {
  id: ID;
  name: string;
  label?: string;
  entity: string;
  expr: string;
  type: "categorical" | "time" | "numeric" | string;
  time_grains?: string[];
  description?: string;
  synonyms?: string[];
}

export interface Entity {
  id: ID;
  name: string;
  table_name: string;
  primary_key: string | string[];
  grain_description?: string;
  label?: string | null;
  description?: string | null;
  default_time_dimension?: string | null;
}

export type DriverRelation = "additive" | "multiplicative" | "ratio_numerator" | "ratio_denominator" | "subtractive";

export interface DriverEdge {
  parent: string;
  child: string;
  relation: DriverRelation | string;
  approved: boolean;
  origin?: "manual" | "suggested" | "demo" | string;
  /** True for engine-suggested edges that are not canonical until approved. */
  suggested?: boolean;
  notes?: string | null;
}

export interface MetricTree {
  id: ID;
  root_metric: string;
  name?: string;
  description?: string;
  nodes: DriverEdge[];
  created_at?: ISODate;
  updated_at?: ISODate;
}

export interface GlossaryTerm {
  id: ID;
  term: string;
  definition: string;
  formula?: string | null;
  metric_name?: string | null;
  metric_id?: string | null;
  related?: string[];
  synonyms?: string[];
  /** When set, the term is ambiguous: it maps to several metrics and interpretation must ask. */
  candidate_metric_ids?: string[];
  updated_at?: ISODate;
}

export interface CalendarConfig {
  fiscal_year_start_month: number;
  week_start: string;
}

export interface SemanticModelDoc {
  version_no?: number;
  content_hash?: string;
  model: Record<string, unknown>;
  yaml?: string;
}

export interface LineageNode {
  id: string;
  kind:
    | "finding"
    | "chart"
    | "query"
    | "artifact"
    | "metric"
    | "metric_version"
    | "dimension"
    | "dataset"
    | "dataset_version"
    | "source"
    | "table"
    | "column"
    | "investigation"
    | "dashboard"
    | "tile"
    | "report"
    | string;
  label: string;
  detail?: string | null;
  ref_id?: string | null;
  meta?: Record<string, unknown>;
}

export interface LineageEdge {
  from: string;
  to: string;
  label?: string | null;
}

export interface LineageGraph {
  nodes: LineageNode[];
  edges: LineageEdge[];
  root?: string;
}

/* ------------------------------------------------------------------ queries / explore */

export interface QueryParam {
  name: string;
  type: "string" | "number" | "date" | "boolean" | string;
  default?: unknown;
  label?: string;
}

export interface QueryRun {
  id: ID;
  origin: string;
  sql: string;
  params?: Record<string, unknown>;
  status: "succeeded" | "failed" | "rejected" | string;
  error?: string | null;
  row_count: number;
  truncated?: boolean;
  elapsed_ms: number;
  columns?: QueryColumn[];
  saved_query_id?: ID | null;
  investigation_id?: ID | null;
  data_source_id?: ID | null;
  dataset_versions?: Record<string, unknown>;
  created_at: ISODate;
}

export interface QueryRunResult extends QueryRun {
  result?: QueryResult | null;
  rows?: unknown[][];
}

export interface SavedQuery {
  id: ID;
  name: string;
  description?: string;
  sql: string;
  parameters: QueryParam[];
  data_source_id?: ID | null;
  tags?: string[];
  chart?: ChartConfig | null;
  version_no?: number;
  created_at: ISODate;
  updated_at?: ISODate;
}

export interface FilterSpec {
  dimension: string;
  op: "eq" | "neq" | "in" | "not_in" | "gt" | "gte" | "lt" | "lte" | "between" | "contains" | "is_null" | "not_null" | string;
  values: unknown[];
}

export interface TimeWindow {
  dimension?: string | null;
  start: string;
  end: string;
  grain?: string | null;
  label?: string | null;
}

export interface MetricQuery {
  metrics: string[];
  dimensions: string[];
  filters?: FilterSpec[];
  time?: TimeWindow | null;
  order_by?: { field: string; direction: "asc" | "desc" }[];
  limit?: number | null;
}

export interface CompiledQuery {
  sql: string;
  params?: unknown[] | Record<string, unknown>;
  grain?: string;
  joins?: { from_entity: string; to_entity: string; cardinality?: string; pre_aggregated?: boolean }[];
  warnings?: string[];
  metric_versions?: Record<string, number | string>;
}

export interface ExploreResult {
  compiled: CompiledQuery;
  result: QueryResult;
  filter_context?: FilterContextItem[];
}

/** Table exploration without SQL, compiled server-side. */
export interface TableExploreRequest {
  table: string;
  filters?: { column: string; op: string; values: unknown[] }[];
  group_by?: string[];
  aggregates?: { column: string | null; fn: string; alias?: string }[];
  calculated?: { name: string; expr: string }[];
  order_by?: { field: string; direction: "asc" | "desc" }[];
  limit?: number;
}

export interface FilterContextItem {
  label: string;
  value: string;
  kind?: "time" | "dimension" | "metric" | "segment" | "baseline" | string;
  inherited?: boolean;
  /** A dashboard filter this tile cannot use (shown struck through, never silently dropped). */
  ignored?: boolean;
  op?: string | null;
}

/* ------------------------------------------------------------------ charts */

export type ChartType =
  | "line"
  | "bar"
  | "stacked_bar"
  | "area"
  | "scatter"
  | "histogram"
  | "box"
  | "heatmap"
  | "waterfall"
  | "kpi"
  | "table";

export interface ChartConfig {
  type: ChartType;
  x?: string | null;
  y?: string[];
  series?: string | null;
  value?: string | null;
  title?: string;
  sort?: { field: string; direction: "asc" | "desc" } | null;
  format?: MetricFormat | string | null;
  horizontal?: boolean;
  compare?: string | null;
}

export interface ChartProvenance {
  dataset?: string | null;
  datasets?: string[];
  metric?: string | null;
  metric_version?: number | string | null;
  dimensions?: string[];
  filters?: FilterContextItem[];
  sort?: string | null;
  calculation?: string | null;
  sql?: string | null;
  python?: string | null;
  artifact_id?: string | null;
  query_run_id?: string | null;
  /** Dashboard tile this chart belongs to; the inspector loads its lineage (tile → metric → dataset → source). */
  tile?: { dashboard_id: string; tile_id: string } | null;
}

/* ------------------------------------------------------------------ notebooks */

export type CellKind = "sql" | "python" | "markdown" | "chart" | "finding";

export interface NotebookCell {
  id: ID;
  position: number;
  kind: CellKind;
  source: string;
  config: Record<string, unknown>;
  output?: CellOutput | null;
  status: "idle" | "ok" | "error" | "running" | string;
  execution_count: number;
  last_run_at?: ISODate | null;
}

export interface CellOutput {
  result?: QueryResult | null;
  stdout?: string;
  stderr?: string;
  error?: string | null;
  dataframes?: Record<string, QueryResult>;
  figures?: string[];
  elapsed_ms?: number;
  chart?: ChartConfig | null;
}

export interface Notebook {
  id: ID;
  title: string;
  description?: string;
  investigation_id?: ID | null;
  cells?: NotebookCell[];
  cell_count?: number;
  created_at: ISODate;
  updated_at?: ISODate;
}

/** `POST .../notebooks/{id}/run`: runs every cell in order and stops at the first error. */
export interface RunAllResult {
  notebook: Notebook;
  executed: number;
  stopped_at: ID | null;
}

export interface PythonResult {
  stdout: string;
  stderr: string;
  error?: string | null;
  dataframes: Record<string, QueryResult>;
  figures: string[];
  elapsed_ms: number;
}

/* ------------------------------------------------------------------ investigations */

export type StatementType = "observation" | "supported_explanation" | "hypothesis";
export type EvidenceStrength = "strong" | "moderate" | "weak" | "hypothesis_only";
export type NodeStatus = "proposed" | "confirmed" | "rejected" | "needs_review" | "failed";

export interface Segment {
  dimension: string;
  value: string | null;
}

/** Signed share of the parent's change (investigator `Contribution`). */
export interface ContributionInfo {
  effect: number;
  share: number | null;
  method: "additive" | "ratio_mix_rate" | "lmdi" | "additive_identity" | "non_additive" | string;
  mix_effect?: number | null;
  rate_effect?: number | null;
}

export interface Annotation {
  id?: string;
  text: string;
  author?: string | null;
  created_at?: ISODate;
}

export interface TreeNode {
  id: string;
  parent_id: string | null;
  statement: string;
  statement_type: StatementType;
  metric_id?: string | null;
  metric_label?: string | null;
  segment?: Segment | null;
  current?: number | null;
  baseline?: number | null;
  abs_change?: number | null;
  pct_change?: number | null;
  contribution_to_parent?: number | ContributionInfo | null;
  evidence_strength: EvidenceStrength;
  evidence_reasons: string[];
  artifact_ids: string[];
  status: NodeStatus;
  children: string[] | TreeNode[];
  annotations?: (Annotation | string)[];
  format?: MetricFormat | string | null;
  metric_format?: MetricFormat | string | null;
  method?: string | null;
  kind?: "root" | "driver" | "dimension" | "segment" | "other" | "check" | "hypothesis" | "failed" | string | null;
  dimension?: string | null;
  segment_path?: Segment[];
  step_id?: string | null;
  notes?: string[];
  rank?: number | null;
  explanatory_power?: number | null;
  finding_id?: string | null;
  /** Analyst decisions carried forward from earlier runs. */
  decision_history?: { from_investigation?: string | null; status?: string | null; carried_as?: string | null; changed?: string[] }[];
  depth?: number;
}

export interface InvestigationTree {
  root_id: string | null;
  nodes: Record<string, TreeNode> | TreeNode[];
}

export interface MetricCandidate {
  metric_id: string;
  label: string;
  description?: string;
  matched_by?: string;
}

export interface Interpretation {
  question?: string;
  metric_ids: string[];
  ambiguous: { term: string; candidates: (string | MetricCandidate)[]; reason?: string; llm_suggestion?: string | null }[];
  unresolved_filters?: { text: string; reason: string }[];
  breakdown_dimensions?: string[];
  direction?: "decrease" | "increase" | "unspecified" | string;
  baseline_metric_id?: string | null;
  resolved_terms?: Record<string, string>;
  template_id?: string | null;
  window?: TimeWindow | null;
  baseline?: TimeWindow | null;
  comparison_kind?: "pop" | "yoy" | "budget" | "forecast" | string | null;
  filters: FilterSpec[];
  intent: "why_change" | "compare" | "breakdown" | "trend" | "lookup" | "forecast" | "anomaly" | string;
  confidence_notes: string[];
  /** Facts the question takes for granted ("revenue was roughly flat"); each is measured and reported. */
  premises?: Premise[];
  /** The question named no period, so the window was chosen (see period_candidates). */
  period_defaulted?: boolean;
  reference_date?: string | null;
  /** "Dallas vs Houston": the same period, one segment against another. */
  segment_comparison?: { dimension: string; current: string; baseline: string } | null;
  /** Rank segments by growth rate ("which products grew fastest") instead of by share of change. */
  ranking?: "growth" | "decline" | null;
  period_candidates?: PeriodCandidate[];
}

export interface Premise {
  text: string;
  term: string;
  metric_id?: string | null;
  expectation: "flat" | "increase" | "decrease";
  tolerance?: number;
}

export interface PeriodCandidate {
  window: TimeWindow;
  baseline: TimeWindow;
  comparison_kind: string;
  premise_changes: Record<string, number | null>;
  subject_change?: number | null;
  holds: boolean;
}

export type PlanStepKind = "decompose" | "segment" | "contribution" | "compare" | "anomaly" | "custom_sql" | "python";

export interface PlanStep {
  id: string;
  kind: PlanStepKind | string;
  title: string;
  rationale: string;
  params: Record<string, unknown>;
  enabled: boolean;
  estimated_queries?: number;
  origin?: "template" | "metric_tree" | "dimension" | "user" | "llm" | string;
}

export interface AnalysisPlan {
  steps: PlanStep[];
  requires_approval: boolean;
  approval_reasons?: string[];
  template_id?: string | null;
  estimated_queries?: number;
  notes?: string[];
  context?: Record<string, unknown> | null;
  config?: Record<string, unknown>;
}

export interface Hypothesis {
  id?: string;
  title?: string;
  category?: string;
  testable?: boolean;
  test_step_ids?: string[];
  reason?: string;
  statement?: string;
  rationale?: string;
  test_kind?: string;
  status?: string;
}

export type InvestigationStatus =
  | "draft"
  | "interpreted"
  | "planned"
  | "needs_disambiguation"
  | "ready"
  | "awaiting_approval"
  | "running"
  | "completed"
  | "failed"
  | string;

export interface Investigation {
  id: ID;
  question: string;
  title: string;
  template?: string | null;
  status: InvestigationStatus;
  interpretation?: Interpretation | null;
  plan?: AnalysisPlan | null;
  tree?: InvestigationTree | null;
  summary?: InvestigationSummary | null;
  hypotheses?: Hypothesis[];
  metric_version_ids?: Record<string, unknown>;
  dataset_versions?: Record<string, unknown>;
  engine_version?: string | null;
  run_count: number;
  current_run_id?: ID | null;
  error?: string | null;
  created_by?: string | null;
  created_at: ISODate;
  updated_at?: ISODate;
  node_count?: number;
  finding_count?: number;
  headline?: string | null;
  brief_answer?: string | null;
  followups?: (string | FollowUp)[];
  failures?: string[];
  /** Present when the optional AI orchestrator ran (spec §51): its stages and what it contributed. */
  orchestration?: {
    stages: { stage: string; source: string; ok: boolean; detail?: string | null; duration_ms?: number | null }[];
    narrative_source?: "template" | "llm_verified" | string | null;
    rejected_outputs?: unknown[];
    tool_calls?: { tool: string; ok: boolean; error?: string | null; artifact_ids?: string[] }[] | number | null;
  plan_steps_added?: unknown[];
  } | null;
  /** tree node id → finding id for nodes saved as findings. */
  node_findings?: Record<string, ID>;
  completed_at?: ISODate | null;
}

export interface InvestigationSummary {
  headline?: string;
  observations?: string[];
  supported_explanations?: string[];
  hypotheses?: string[];
  follow_ups?: FollowUp[];
}

export interface FollowUp {
  label: string;
  command?: string;
  node_id?: string | null;
  dimension?: string | null;
}

export interface InvestigationRun {
  id: ID;
  run_no: number;
  status: string;
  error?: string | null;
  duration_ms?: number;
  started_at: ISODate;
  finished_at?: ISODate | null;
  diff?: InvestigationDiff | null;
}

export interface InvestigationDiff {
  from_run?: number | null;
  to_run?: number | null;
  previous_investigation_id?: string | null;
  unchanged_nodes?: number;
  summary?: string[];
  node_changes: {
    node_id?: string;
    statement?: string;
    statement_before?: string | null;
    statement_after?: string | null;
    change: "added" | "removed" | "changed" | "unchanged" | string;
    before?: Partial<TreeNode> | null;
    after?: Partial<TreeNode> | null;
    /** Either a list of changed field names or `{field: [before, after]}` (investigator NodeChange). */
    fields?: string[] | Record<string, [unknown, unknown]>;
  }[];
  dataset_version_changes: {
    dataset?: string;
    table?: string;
    before?: (Partial<DatasetVersion> & { table?: string }) | null;
    after?: (Partial<DatasetVersion> & { table?: string }) | null;
  }[];
  metric_version_changes: {
    metric?: string;
    metric_id?: string;
    before?: number | string | null;
    after?: number | string | null;
  }[];
}

export interface ValidationCheck {
  name: string;
  passed: boolean;
  detail?: string | null;
}

export interface ArtifactRecord {
  id: ID;
  /** Engine-side id; investigation TreeNode.artifact_ids reference this. */
  engine_id?: string | null;
  kind: "query" | "dataframe" | "metric_result" | "chart" | "statistical_test" | "finding" | "hypothesis" | "note" | string;
  title?: string;
  sql?: string | null;
  python?: string | null;
  params?: Record<string, unknown>;
  filters?: (FilterSpec | FilterContextItem)[];
  filter_context?: FilterContextItem[];
  metric_versions?: Record<string, unknown>;
  metric_ids?: string[];
  dataset_versions?:
    | Record<string, unknown>
    | { table: string; content_hash: string; row_count: number; captured_at?: ISODate | null }[];
  window?: TimeWindow | null;
  result?: QueryResult | Record<string, unknown> | null;
  data?: Record<string, unknown>;
  chart_spec?: Record<string, unknown> | null;
  validation?: { checks: ValidationCheck[]; ok: boolean } | null;
  warnings?: string[];
  error?: string | null;
  parent_ids?: string[];
  investigation_id?: ID | null;
  run_id?: ID | null;
  created_at: ISODate;
}

export type CommandAction =
  | "drilled"
  | "branched"
  | "created_investigation"
  | "saved_finding"
  | "show_sql"
  | "built_report"
  | "built_dashboard"
  | "rerun_started"
  | "node_updated"
  | "listed_contributors"
  | "clarify"
  | "none";

/** One segment node returned by breakdown/drill commands. */
export interface CommandItem {
  node_id: string;
  statement?: string;
  segment?: { dimension: string; value: unknown } | null;
  current?: number | null;
  baseline?: number | null;
  abs_change?: number | null;
  pct_change?: number | null;
  share?: number | null;
  evidence_strength?: string | null;
}

/** `POST .../command` response, exactly as the API returns it (schemas/investigations.py `CommandResult`). */
export interface CommandResult {
  command: { kind: string; unresolved?: string[]; [k: string]: unknown };
  message: string;
  action: CommandAction;
  investigation_id?: ID | null;
  finding_id?: ID | null;
  report_id?: ID | null;
  dashboard_id?: ID | null;
  job_id?: ID | null;
  sql?: { artifact_id?: ID | null; engine_id?: string; title?: string; sql: string }[];
  items?: CommandItem[];
}

/* ------------------------------------------------------------------ findings */

export type FindingStatus = "draft" | "confirmed" | "rejected" | "needs_review";

export interface Finding {
  id: ID;
  statement: string;
  statement_type: StatementType;
  evidence_strength: EvidenceStrength;
  evidence_reasons: string[];
  status: FindingStatus;
  notes?: string;
  business_impact?: string;
  metric_id?: string | null;
  metric_label?: string | null;
  metric_version_ids?: Record<string, unknown>;
  values?: {
    current?: number | null;
    baseline?: number | null;
    abs_change?: number | null;
    pct_change?: number | null;
    share?: number | null;
    format?: string | null;
  };
  filter_context: FilterContextItem[];
  segment?: Segment | null;
  artifact_ids: string[];
  tags?: string[];
  investigation_id?: ID | null;
  investigation_title?: string | null;
  node_id?: string | null;
  version_no: number;
  created_by?: string | null;
  created_by_name?: string | null;
  created_at: ISODate;
  updated_at?: ISODate;
  comment_count?: number;
}

export interface FindingComment {
  id: ID;
  body: string;
  user_id?: ID | null;
  user_name?: string | null;
  created_at: ISODate;
}

export interface FindingVersion {
  id: ID;
  version_no: number;
  snapshot: Partial<Finding>;
  change_note?: string;
  created_at: ISODate;
  created_by?: string | null;
}

/* ------------------------------------------------------------------ outputs */

export type TileKind = "kpi" | "chart" | "table" | "text";

export interface TileBinding {
  source?: "metric" | "saved_query" | "artifact" | "sql" | string;
  metric?: string | null;
  metrics?: string[];
  dimensions?: string[];
  filters?: FilterSpec[];
  time_dimension?: string | null;
  grain?: string | null;
  saved_query_id?: ID | null;
  artifact_id?: ID | null;
  sql?: string | null;
  compare?: "pop" | "yoy" | "none" | string | null;
  limit?: number | null;
}

export interface DashboardTile {
  id: ID;
  kind: TileKind;
  title: string;
  binding: TileBinding;
  viz: Partial<ChartConfig> & Record<string, unknown>;
  text?: string;
}

export interface LayoutItem {
  i: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface DashboardFilterControl {
  dimension: string;
  label?: string;
  values: unknown[];
}

export interface Dashboard {
  id: ID;
  name: string;
  description?: string;
  layout: LayoutItem[];
  filters: DashboardFilterControl[];
  date_range?: { start?: string | null; end?: string | null; preset?: string | null; dimension?: string | null } | null;
  tiles?: DashboardTile[];
  tile_count?: number;
  version_no: number;
  created_at: ISODate;
  updated_at?: ISODate;
}

export interface TileData {
  tile_id: ID;
  result?: QueryResult | null;
  compiled?: CompiledQuery | null;
  kpi?: { value: number | null; baseline?: number | null; pct_change?: number | null; format?: string | null } | null;
  filter_context?: FilterContextItem[];
  error?: string | null;
  provenance?: ChartProvenance;
}

export type ReportBlockKind =
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

export interface ReportBlock {
  id: string;
  kind: ReportBlockKind | string;
  title?: string;
  text?: string;
  finding_id?: ID | null;
  artifact_id?: ID | null;
  tile?: Omit<DashboardTile, "id"> | null;
  data?: Record<string, unknown> | null;
  items?: unknown[];
  reviewed?: boolean;
  included?: boolean;
}

export interface Report {
  id: ID;
  title: string;
  kind: "custom" | "business_review" | "investigation" | string;
  status: "draft" | "in_review" | "published" | string;
  period?: string | null;
  investigation_id?: ID | null;
  blocks: ReportBlock[];
  version_no: number;
  published_at?: ISODate | null;
  created_at: ISODate;
  updated_at?: ISODate;
}

export interface ExecutiveSummary {
  observations: SummaryItem[];
  supported_explanations: SummaryItem[];
  hypotheses: SummaryItem[];
  text?: string;
  llm_used?: boolean;
}

export interface SummaryItem {
  text: string;
  finding_id?: ID | null;
  evidence_strength?: EvidenceStrength | null;
}

export type ExportFormat = "csv" | "xlsx" | "png" | "pdf" | "md" | "html" | "ipynb" | "json";

export interface ExportFile {
  id: ID;
  format: ExportFormat | string;
  filename: string;
  media_type: string;
  size_bytes: number;
  download_url: string;
  created_at: ISODate;
}

/* ------------------------------------------------------------------ platform */

export interface SearchHit {
  id: ID;
  kind:
    | "dataset"
    | "table"
    | "column"
    | "metric"
    | "dimension"
    | "glossary"
    | "investigation"
    | "finding"
    | "dashboard"
    | "report"
    | "saved_query"
    | "notebook"
    | string;
  title: string;
  subtitle?: string | null;
  snippet?: string | null;
  ref_id?: ID | null;
  parent_id?: ID | null;
  score?: number;
}

export interface AISettings {
  enabled: boolean;
  provider: "anthropic" | "none" | string;
  model_large?: string | null;
  model_default?: string | null;
  model_small?: string | null;
  /** When false only schemas, metric definitions and aggregates are sent to the provider. */
  allow_result_samples?: boolean;
  monthly_budget_usd?: number | null;
  /** Lets the orchestrator run bounded read-only tool calls (list tables, inspect metrics, run SQL). */
  tool_loop?: boolean;
  month_spend_usd?: number | null;
  over_budget?: boolean;
  /** False while AI is paused (disabled, no key, or the monthly budget is reached). */
  active?: boolean;
  has_api_key?: boolean;
  key_source?: "workspace" | "environment" | "none" | string;
  server_ai_enabled?: boolean;
}

export interface AIUsageRow {
  id?: ID;
  provider: string;
  model: string;
  task: string;
  tokens_in: number;
  tokens_out: number;
  latency_ms: number;
  est_cost_usd: number;
  success?: boolean;
  created_at: ISODate;
}

export interface AIUsageBreakdown {
  calls: number;
  tokens_in?: number;
  tokens_out?: number;
  est_cost_usd?: number;
  model?: string;
  task?: string;
}

export interface AIUsageSummary {
  items: AIUsageRow[];
  totals: { calls: number; tokens_in: number; tokens_out: number; est_cost_usd: number };
  by_task: AIUsageBreakdown[];
  by_model: AIUsageBreakdown[];
}

export interface DiagnosticsInfo {
  [k: string]: unknown;
}

export interface DiagnosticsSummaryRow {
  category: string;
  count?: number;
  errors?: number;
  error_rate?: number | null;
  p50_ms?: number | null;
  p95_ms?: number | null;
  [k: string]: unknown;
}

export interface EventLogEntry {
  id: ID;
  category: string;
  name: string;
  status: "ok" | "error" | "rejected" | string;
  duration_ms?: number | null;
  detail?: Record<string, unknown> | string | null;
  error?: string | null;
  request_id?: string | null;
  user_id?: ID | null;
  created_at: ISODate;
}

export interface AuditEntry {
  id: ID;
  actor?: string | null;
  user_id?: ID | null;
  action: string;
  resource_type?: string | null;
  resource_id?: ID | null;
  detail?: Record<string, unknown> | null;
  ip?: string | null;
  request_id?: string | null;
  created_at: ISODate;
}

export interface HomeChange {
  metric_id: string;
  label: string;
  format?: string | null;
  period?: string | null;
  current: number | null;
  baseline: number | null;
  abs_change?: number | null;
  pct_change?: number | null;
  sql?: string | null;
}

export interface HomeDataset {
  id: ID;
  name: string;
  table_name: string;
  row_count: number;
  updated_at?: ISODate | null;
  profile_status?: string;
  issue_count?: number | null;
}

export interface HomeQualityFailure {
  id?: ID;
  rule_id?: ID;
  name?: string;
  rule_name?: string;
  table_name?: string;
  dataset_id?: ID | null;
  severity?: string;
  failing_count?: number | null;
  last_run_at?: ISODate | null;
  [k: string]: unknown;
}

export interface HomeSummary {
  workspace?: Workspace;
  counts?: Record<string, number>;
  datasets: HomeDataset[];
  recent_investigations: Investigation[];
  recent_findings: Finding[];
  quality_failures: HomeQualityFailure[];
  dashboards: Dashboard[];
  pending_relationships?: number;
  running_jobs?: Job[];
  changes: HomeChange[];
}

export interface WorkspaceSettingsDoc {
  calendar: { fiscal_year_start_month: number; week_start: "monday" | "sunday" | string };
  ai: AISettings;
  investigation: { max_depth: number; top_segments: number; require_plan_approval: boolean };
}

export interface CalendarResolution {
  input: string;
  window: TimeWindow;
  previous_period: TimeWindow;
  same_period_last_year: TimeWindow;
}

export interface DemoAvailability {
  available: boolean;
  workspaces: { id: ID; name: string }[];
}

export interface DemoStatus {
  loaded: boolean;
  workspace_id?: ID | null;
  message?: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit?: number;
  offset?: number;
}
