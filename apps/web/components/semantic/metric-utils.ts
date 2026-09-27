/** Pure helpers for the semantic-layer screens (metric form logic, formulas, ambiguity, driver trees). */
import type { DriverEdge, DriverRelation, GlossaryTerm, Metric, MetricDefinition, TimeAggregation } from "@/lib/api/types";
import type { MetricInput, SemanticModelSpec } from "@/lib/api/resources/semantic";

export type MetricKind = "simple" | "ratio" | "derived";
export type Agg = "sum" | "count" | "count_distinct" | "avg" | "min" | "max";

export const AGGS: Agg[] = ["sum", "count", "count_distinct", "avg", "min", "max"];
export const FORMATS = ["currency", "number", "percent", "integer"] as const;

export const TIME_AGGREGATIONS: { value: TimeAggregation; label: string; help: string }[] = [
  { value: "sum", label: "Sum over time (flow)", help: "Revenue, orders, units: periods add up." },
  { value: "last", label: "Last value in period (balance)", help: "Inventory, headcount: only the latest snapshot in each period counts." },
  { value: "first", label: "First value in period (opening balance)", help: "Only the earliest snapshot in each period counts." },
  { value: "avg", label: "Average of daily totals", help: "Average over the dates in the period of the per-date total." },
];

export interface MetricForm {
  id: string;
  label: string;
  description: string;
  kind: MetricKind;
  entity: string;
  agg: Agg;
  time_aggregation: TimeAggregation;
  expr: string;
  numerator: string;
  denominator: string;
  formula: string;
  format: (typeof FORMATS)[number];
  owner: string;
  tags: string;
  synonyms: string;
  canonical: boolean;
  higher_is_better: boolean;
  default_time_dimension: string;
  change_note: string;
}

export const emptyMetricForm = (): MetricForm => ({
  id: "",
  label: "",
  description: "",
  kind: "simple",
  entity: "",
  agg: "sum",
  time_aggregation: "sum",
  expr: "",
  numerator: "",
  denominator: "",
  formula: "",
  format: "number",
  owner: "",
  tags: "",
  synonyms: "",
  canonical: true,
  higher_is_better: true,
  default_time_dimension: "",
  change_note: "",
});

export function formFromMetric(m: Metric): MetricForm {
  return {
    id: m.id,
    label: m.label ?? "",
    description: m.description ?? "",
    kind: (m.kind as MetricKind) ?? "simple",
    entity: m.entity ?? "",
    agg: ((m.agg as Agg) ?? "sum") as Agg,
    time_aggregation: m.time_aggregation ?? "sum",
    expr: m.expr ?? "",
    numerator: m.numerator ?? "",
    denominator: m.denominator ?? "",
    formula: m.formula ?? "",
    format: (FORMATS as readonly string[]).includes(m.format) ? (m.format as MetricForm["format"]) : "number",
    owner: m.owner ?? "",
    tags: (m.tags ?? []).join(", "),
    synonyms: (m.synonyms ?? []).join(", "),
    canonical: m.canonical ?? true,
    higher_is_better: m.higher_is_better ?? true,
    default_time_dimension: m.default_time_dimension ?? "",
    change_note: "",
  };
}

export const splitList = (s: string): string[] =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

/** Machine name from a label: "Gross Margin %" → "gross_margin_pct". */
export function slugifyMetricId(label: string): string {
  return label
    .toLowerCase()
    .replace(/%/g, " pct ")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/^(\d)/, "m_$1");
}

/** Field-level validation that mirrors the engine's rules per metric kind. */
export function validateMetricForm(f: MetricForm): Partial<Record<keyof MetricForm, string>> {
  const errs: Partial<Record<keyof MetricForm, string>> = {};
  if (!f.id.trim()) errs.id = "Required";
  else if (!/^[a-z][a-z0-9_]*$/.test(f.id)) errs.id = "Lowercase letters, digits and underscores; start with a letter";
  if (!f.label.trim()) errs.label = "Required";
  if (f.kind === "simple") {
    if (!f.entity) errs.entity = "Choose the entity (table grain) this metric aggregates";
    if (!f.expr.trim() && f.agg !== "count") errs.expr = "Expression is required unless the aggregation is count";
    if (f.time_aggregation === "avg" && !["sum", "count", "count_distinct"].includes(f.agg))
      errs.time_aggregation = "Averaging over time needs a sum or count aggregation";
  } else if (f.kind === "ratio") {
    if (!f.numerator) errs.numerator = "Choose the numerator metric";
    if (!f.denominator) errs.denominator = "Choose the denominator metric";
    if (f.numerator && f.numerator === f.denominator) errs.denominator = "Numerator and denominator must differ";
    if (f.numerator && f.numerator === f.id) errs.numerator = "A ratio cannot reference itself";
  } else if (f.kind === "derived") {
    if (!f.formula.trim()) errs.formula = "Formula over other metric ids, e.g. revenue - cogs";
  }
  return errs;
}

/** Only the fields relevant to the chosen kind are sent; the others are cleared so no stale logic survives. */
export function metricPayloadFromForm(f: MetricForm): MetricInput {
  const base: MetricInput = {
    id: f.id.trim(),
    name: f.id.trim(),
    label: f.label.trim(),
    description: f.description.trim(),
    kind: f.kind,
    format: f.format,
    owner: f.owner.trim() || null,
    tags: splitList(f.tags),
    synonyms: splitList(f.synonyms),
    canonical: f.canonical,
    higher_is_better: f.higher_is_better,
    default_time_dimension: f.default_time_dimension || null,
    entity: null,
    agg: null,
    expr: null,
    numerator: null,
    denominator: null,
    formula: null,
    time_aggregation: "sum",
  };
  if (f.change_note.trim()) base.change_note = f.change_note.trim();
  if (f.kind === "simple") {
    base.entity = f.entity || null;
    base.agg = f.agg;
    base.expr = f.expr.trim() || null;
    base.time_aggregation = f.time_aggregation;
  } else if (f.kind === "ratio") {
    base.numerator = f.numerator;
    base.denominator = f.denominator;
  } else {
    base.formula = f.formula.trim();
  }
  return base;
}

/** One-line human formula: "SUM(net_amount) on order_line", "revenue ÷ orders", "revenue - cogs". */
export function formulaSummary(
  m: Pick<MetricDefinition, "kind" | "agg" | "expr" | "entity" | "numerator" | "denominator" | "formula" | "time_aggregation">,
): string {
  if (m.kind === "ratio") return `${m.numerator ?? "?"} ÷ ${m.denominator ?? "?"}`;
  if (m.kind === "derived") return m.formula ?? "";
  const agg = (m.agg ?? "sum").toUpperCase();
  const inner = m.expr ?? (m.agg === "count" ? "*" : "?");
  const call = agg === "COUNT_DISTINCT" ? `COUNT(DISTINCT ${inner})` : `${agg}(${inner})`;
  const base = m.entity ? `${call} per ${m.entity}` : call;
  return m.time_aggregation && m.time_aggregation !== "sum" ? `${base}, ${timeAggregationPhrase(m.time_aggregation)}` : base;
}

/** Short phrase for a semi-additive rule, e.g. "last value in each period". */
export function timeAggregationPhrase(t: TimeAggregation | null | undefined): string {
  if (t === "last") return "last value in each period";
  if (t === "first") return "first value in each period";
  if (t === "avg") return "average of daily totals in each period";
  return "summed over time";
}

/** Metric ids referenced by a derived formula (tokens that match known metrics). */
export function formulaRefs(formula: string, known: string[]): string[] {
  const set = new Set(known);
  const out: string[] = [];
  for (const tok of formula.match(/[A-Za-z_][A-Za-z0-9_]*/g) ?? []) if (set.has(tok) && !out.includes(tok)) out.push(tok);
  return out;
}

export interface SynonymConflict {
  term: string;
  metricIds: string[];
}

/**
 * Terms (labels or synonyms) that resolve to more than one metric. The investigation engine must ask
 * rather than silently choose (spec §15), so the UI surfaces these as ambiguity warnings.
 */
export function findSynonymConflicts(metrics: Pick<Metric, "id" | "label" | "name" | "synonyms">[]): SynonymConflict[] {
  const map = new Map<string, Set<string>>();
  const add = (t: string | null | undefined, id: string) => {
    const k = (t ?? "").trim().toLowerCase();
    if (!k) return;
    if (!map.has(k)) map.set(k, new Set());
    map.get(k)!.add(id);
  };
  for (const m of metrics) {
    (m.synonyms ?? []).forEach((s) => add(s, m.id));
  }
  // A synonym that equals another metric's label is also a conflict.
  for (const m of metrics) {
    const k = (m.label ?? "").trim().toLowerCase();
    if (k && map.has(k)) map.get(k)!.add(m.id);
  }
  return [...map.entries()]
    .filter(([, ids]) => ids.size > 1)
    .map(([term, ids]) => ({ term, metricIds: [...ids].sort() }))
    .sort((a, b) => a.term.localeCompare(b.term));
}

/** Candidate metrics for a glossary term: explicit candidates, or conflicts on its term/synonyms. */
export function termCandidates(term: GlossaryTerm, conflicts: SynonymConflict[]): string[] {
  if (term.candidate_metric_ids && term.candidate_metric_ids.length > 1) return term.candidate_metric_ids;
  const keys = [term.term, ...(term.synonyms ?? [])].map((s) => s.trim().toLowerCase());
  const hit = conflicts.find((c) => keys.includes(c.term));
  return hit ? hit.metricIds : [];
}

/* ------------------------------------------------------------------ driver trees */

export const RELATION_META: Record<DriverRelation, { label: string; symbol: string; description: string }> = {
  additive: { label: "Additive", symbol: "+", description: "Parent is the sum of these children." },
  subtractive: { label: "Subtractive", symbol: "−", description: "Child is subtracted from the parent." },
  multiplicative: { label: "Multiplicative", symbol: "×", description: "Parent is the product of these children." },
  ratio_numerator: { label: "Ratio numerator", symbol: "÷ num", description: "Child is the numerator of the parent ratio." },
  ratio_denominator: { label: "Ratio denominator", symbol: "÷ den", description: "Child is the denominator of the parent ratio." },
};

export const RELATIONS = Object.keys(RELATION_META) as DriverRelation[];

export function relationMeta(r: string) {
  return RELATION_META[r as DriverRelation] ?? { label: r, symbol: "?", description: r };
}

export const isSuggestedEdge = (e: DriverEdge): boolean => !!e.suggested || !e.approved;

export interface TreeRow {
  metric: string;
  depth: number;
  edge: DriverEdge | null;
  /** Path of metric ids from root to this node (used as a stable key). */
  path: string[];
  cycle: boolean;
}

/** Depth-first rows for rendering a driver tree from its edge list (cycle-safe). */
export function buildTreeRows(root: string, edges: DriverEdge[]): TreeRow[] {
  const byParent = new Map<string, DriverEdge[]>();
  for (const e of edges) {
    const arr = byParent.get(e.parent);
    if (arr) arr.push(e);
    else byParent.set(e.parent, [e]);
  }
  const rows: TreeRow[] = [];
  const walk = (metric: string, depth: number, edge: DriverEdge | null, path: string[]) => {
    const cycle = path.includes(metric);
    const nextPath = [...path, metric];
    rows.push({ metric, depth, edge, path: nextPath, cycle });
    if (cycle || depth > 12) return;
    for (const child of byParent.get(metric) ?? []) walk(child.child, depth + 1, child, nextPath);
  };
  walk(root, 0, null, []);
  return rows;
}

/** Edges not reachable from the root (e.g. a parent that is not in the tree yet). */
export function orphanEdges(root: string, edges: DriverEdge[]): DriverEdge[] {
  const reach = new Set(buildTreeRows(root, edges).map((r) => r.metric));
  return edges.filter((e) => !reach.has(e.parent));
}

export const edgeKey = (e: Pick<DriverEdge, "parent" | "child">) => `${e.parent}→${e.child}`;

/** Merge engine suggestions into the working edge list without overwriting existing edges. */
export function mergeSuggestions(current: DriverEdge[], suggestions: DriverEdge[]): DriverEdge[] {
  const have = new Set(current.map(edgeKey));
  const added = suggestions
    .filter((s) => !have.has(edgeKey(s)))
    .map((s) => ({ ...s, approved: false, suggested: true }));
  return [...current, ...added];
}

/* ------------------------------------------------------------------ model reachability */

/** Base entities a metric aggregates over (following ratio / derived inputs). */
export function metricBaseEntities(model: SemanticModelSpec, id: string, seen = new Set<string>()): string[] {
  if (seen.has(id)) return [];
  seen.add(id);
  const m = model.metrics.find((x) => x.id === id);
  if (!m) return [];
  if (m.kind === "ratio") return [...new Set([m.numerator, m.denominator].flatMap((x) => (x ? metricBaseEntities(model, x, seen) : [])))];
  if (m.kind === "derived")
    return [
      ...new Set(
        formulaRefs(m.formula ?? "", model.metrics.map((x) => x.id)).flatMap((x) => metricBaseEntities(model, x, seen)),
      ),
    ];
  return m.entity ? [m.entity] : [];
}

/** Entities reachable through approved many-to-one / one-to-one relationships (safe, no fan-out). */
export function reachableEntities(model: SemanticModelSpec, start: string[]): string[] {
  const out = new Set(start);
  const queue = [...start];
  while (queue.length) {
    const cur = queue.shift()!;
    for (const r of model.relationships) {
      if (!r.approved) continue;
      let next: string | null = null;
      if (r.from_entity === cur && (r.cardinality === "many_to_one" || r.cardinality === "one_to_one")) next = r.to_entity;
      if (r.to_entity === cur && (r.cardinality === "one_to_many" || r.cardinality === "one_to_one")) next = r.from_entity;
      if (next && !out.has(next)) {
        out.add(next);
        queue.push(next);
      }
    }
  }
  return [...out];
}

