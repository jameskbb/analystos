/**
 * Pure helpers that normalize the investigation payload into a shape the UI can render
 * without re-deriving numbers. The investigator stores the tree flat (`nodes` with
 * `parent_id`/`children` ids); older payloads may use a node map or nested children.
 */
import type {
  Annotation,
  ArtifactRecord,
  ContributionInfo,
  FilterContextItem,
  FilterSpec,
  FollowUp,
  Interpretation,
  Investigation,
  InvestigationTree,
  MetricCandidate,
  QueryResult,
  TimeWindow,
  TreeNode,
  PeriodCandidate,
} from "@/lib/api/types";
import { formatWindow, humanize } from "@/lib/format";

export interface NormNode extends Omit<TreeNode, "children" | "contribution_to_parent" | "annotations"> {
  children: string[];
  share: number | null;
  contribution: ContributionInfo | null;
  annotations: Annotation[];
  format: string | null;
}

export interface NormTree {
  rootId: string | null;
  nodes: Map<string, NormNode>;
}

function normContribution(c: TreeNode["contribution_to_parent"]): { share: number | null; info: ContributionInfo | null } {
  if (c === null || c === undefined) return { share: null, info: null };
  if (typeof c === "number") return { share: c, info: { effect: 0, share: c, method: "additive" } };
  return { share: c.share ?? null, info: c };
}

/**
 * Flattens the API tree into a map. `nodeFindings` (Investigation.node_findings: node id → finding id)
 * fills `finding_id` when the node itself does not carry it, so a saved node always shows its link.
 */
export function normalizeTree(
  tree: InvestigationTree | null | undefined,
  nodeFindings?: Record<string, string> | null,
): NormTree {
  const nodes = new Map<string, NormNode>();
  if (!tree || !tree.nodes) return { rootId: null, nodes };
  const raw: TreeNode[] = Array.isArray(tree.nodes) ? tree.nodes : Object.values(tree.nodes);

  const visit = (n: TreeNode, parent: string | null) => {
    const childIds: string[] = [];
    for (const c of n.children ?? []) {
      if (typeof c === "string") childIds.push(c);
      else {
        childIds.push(c.id);
        visit(c, n.id);
      }
    }
    const { share, info } = normContribution(n.contribution_to_parent);
    nodes.set(n.id, {
      ...n,
      parent_id: n.parent_id ?? parent,
      children: childIds,
      share,
      contribution: info,
      annotations: (n.annotations ?? []).map((a) => (typeof a === "string" ? { text: a } : a)),
      format: n.metric_format ?? n.format ?? null,
      finding_id: n.finding_id ?? nodeFindings?.[n.id] ?? null,
    });
  };
  raw.forEach((n) => visit(n, n.parent_id ?? null));
  // Drop dangling child ids so the tree never renders holes.
  nodes.forEach((n) => {
    n.children = n.children.filter((c) => nodes.has(c));
  });
  let rootId = tree.root_id ?? null;
  if (!rootId || !nodes.has(rootId)) rootId = [...nodes.values()].find((n) => !n.parent_id)?.id ?? null;
  return { rootId, nodes };
}

/** Visible nodes in pre-order given the expanded set (used for keyboard navigation). */
export function visibleNodes(tree: NormTree, expanded: Set<string>): { id: string; depth: number }[] {
  const out: { id: string; depth: number }[] = [];
  if (!tree.rootId) return out;
  const rec = (id: string, depth: number) => {
    const n = tree.nodes.get(id);
    if (!n) return;
    out.push({ id, depth });
    if (expanded.has(id)) n.children.forEach((c) => rec(c, depth + 1));
  };
  rec(tree.rootId, 0);
  return out;
}

/** Default expansion: root and every node down to `depth` (inclusive). */
export function defaultExpanded(tree: NormTree, depth = 2): Set<string> {
  const s = new Set<string>();
  const rec = (id: string, d: number) => {
    const n = tree.nodes.get(id);
    if (!n || d >= depth) return;
    if (n.children.length) s.add(id);
    n.children.forEach((c) => rec(c, d + 1));
  };
  if (tree.rootId) rec(tree.rootId, 0);
  return s;
}

export function ancestors(tree: NormTree, id: string): string[] {
  const out: string[] = [];
  let cur = tree.nodes.get(id)?.parent_id ?? null;
  while (cur && !out.includes(cur)) {
    out.push(cur);
    cur = tree.nodes.get(cur)?.parent_id ?? null;
  }
  return out;
}

export const RUNNING_STATUSES = new Set(["running", "queued"]);
export const NEEDS_INPUT_STATUSES = new Set(["needs_disambiguation", "awaiting_approval", "planned", "interpreted", "draft", "ready"]);

export function needsPlanReview(inv: Investigation): boolean {
  const hasTree = normalizeTree(inv.tree).nodes.size > 0;
  if (hasTree) return false;
  return inv.status !== "running" && inv.status !== "failed" && inv.status !== "completed";
}

export function candidateInfo(c: string | MetricCandidate): MetricCandidate {
  return typeof c === "string" ? { metric_id: c, label: humanize(c) } : c;
}

export function windowLabel(w: TimeWindow | null | undefined): string {
  if (!w) return "n/a";
  // Windows are half-open [start, end); show an inclusive end date to humans.
  const end = inclusiveEnd(w.end);
  const range = formatWindow(w.start, end);
  return w.label ? `${w.label} (${range})` : range;
}

export function inclusiveEnd(end: string): string {
  const d = new Date(`${end.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return end;
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}

/** Filter context chips for an investigation or node: period, baseline, filters, segment path. */
export function contextChips(
  interp: Interpretation | null | undefined,
  node?: NormNode | null,
): FilterContextItem[] {
  const out: FilterContextItem[] = [];
  if (interp?.window) out.push({ label: "Period", value: windowLabel(interp.window), kind: "time" });
  if (interp?.baseline)
    out.push({
      label: interp.comparison_kind === "yoy" ? "Baseline (YoY)" : "Baseline",
      value: windowLabel(interp.baseline),
      kind: "baseline",
    });
  for (const f of interp?.filters ?? []) out.push(filterChip(f));
  const path = node?.segment_path?.length ? node.segment_path : node?.segment ? [node.segment] : [];
  for (const s of path) out.push({ label: humanize(s.dimension), value: s.value ?? "(null)", kind: "segment", inherited: s !== path[path.length - 1] });
  return out;
}

export function filterChip(f: FilterSpec): FilterContextItem {
  const vals = (f.values ?? []).map(String).join(", ");
  const op = f.op;
  const value =
    op === "is_null" ? "is empty" : op === "not_null" ? "is not empty" : op === "neq" || op === "not_in" ? `excludes ${vals}` : vals;
  return { label: humanize(f.dimension), value, kind: "dimension" };
}

export function followUpItems(inv: Investigation): FollowUp[] {
  const raw = inv.followups ?? inv.summary?.follow_ups ?? [];
  return raw.map((f) => (typeof f === "string" ? { label: f, command: f } : f));
}

export function headline(inv: Investigation): string | null {
  return inv.brief_answer ?? inv.summary?.headline ?? inv.headline ?? null;
}

/** Extracts a tabular result from an artifact (investigator ResultSnapshot or QueryResult). */
export function artifactResult(a: ArtifactRecord | null | undefined): QueryResult | null {
  const r = a?.result as Partial<QueryResult> | null | undefined;
  if (!r || !Array.isArray(r.columns) || !Array.isArray(r.rows)) return null;
  return {
    columns: r.columns.map((c) => ({ name: String((c as { name: string }).name), type: String((c as { type?: string }).type ?? "") })),
    rows: r.rows,
    row_count: r.row_count ?? r.rows.length,
    truncated: !!r.truncated,
    elapsed_ms: r.elapsed_ms ?? 0,
    sql: a?.sql ?? undefined,
  };
}

export interface DatasetVersionRow {
  table: string;
  content_hash: string;
  row_count: number | null;
  captured_at?: string | null;
}

export function datasetVersions(a: ArtifactRecord | Investigation | null | undefined): DatasetVersionRow[] {
  const dv = a?.dataset_versions as unknown;
  if (!dv) return [];
  if (Array.isArray(dv)) return dv as DatasetVersionRow[];
  return Object.entries(dv as Record<string, unknown>).map(([table, v]) => {
    const o = (v && typeof v === "object" ? v : { content_hash: String(v) }) as Partial<DatasetVersionRow>;
    return { table, content_hash: o.content_hash ?? "", row_count: o.row_count ?? null, captured_at: o.captured_at ?? null };
  });
}

/** Metric version refs come as a number, a version id, or `{version_id, version_no}`. */
export function versionLabel(v: unknown): string {
  if (v && typeof v === "object") {
    const o = v as { version_no?: unknown; version_id?: unknown; version?: unknown };
    if (o.version_no !== undefined && o.version_no !== null) return `v${String(o.version_no)}`;
    if (o.version !== undefined && o.version !== null) return `v${String(o.version)}`;
    if (o.version_id) return String(o.version_id).slice(0, 12);
    return JSON.stringify(v);
  }
  if (typeof v === "number" || (typeof v === "string" && /^\d+$/.test(v))) return `v${v}`;
  return String(v ?? "");
}

export function metricVersions(a: { metric_versions?: Record<string, unknown>; metric_version_ids?: Record<string, unknown> } | null | undefined): [string, string][] {
  const mv = a?.metric_versions ?? a?.metric_version_ids ?? {};
  return Object.entries(mv).map(([k, v]) => [k, versionLabel(v)]);
}

export const METHOD_LABEL: Record<string, string> = {
  additive: "Additive attribution: segment changes sum exactly to the parent change.",
  additive_identity: "Additive identity decomposition (e.g. Margin = Revenue − Cost).",
  lmdi: "Multiplicative decomposition via LMDI (log-mean Divisia): driver effects sum exactly to the total change.",
  volume_mix_rate: "Volume, mix and rate: each segment's effect splits into a mix (share shift) and a rate effect; effects sum to the parent change.",
  ratio_mix_rate: "Ratio metric split into mix (share of volume) and rate (within-segment ratio) effects over the true totals.",
  shapley: "Shapley decomposition: each driver's average marginal effect; effects sum to the parent change.",
  ratio_lmdi: "Ratio decomposition via LMDI: numerator and denominator effects sum exactly to the ratio's change.",
  ratio_shapley: "Ratio decomposition via Shapley: numerator and denominator effects sum to the ratio's change.",
  non_additive:
    "Not additive: the segments overlap (e.g. one order spans several categories), so no share of the parent change is claimed; only segment-level values are shown.",
};

/** A premise check node ("revenue was roughly flat") reports whether the premise held over the periods. */
export function premiseVerdict(node: { kind?: string | null; notes?: string[] | null }): "holds" | "contradicted" | null {
  if (node.kind !== "check") return null;
  const n = (node.notes ?? []).find((x) => /^premise (holds|contradicted)$/.test(x));
  return n ? (n.endsWith("holds") ? "holds" : "contradicted") : null;
}

/** How a period candidate compares, for the "why this period" list. */
export function periodCandidateLabel(c: PeriodCandidate): string {
  const w = `${formatWindow(c.window.start, inclusiveEnd(c.window.end))} vs ${formatWindow(c.baseline.start, inclusiveEnd(c.baseline.end))}`;
  const changes = Object.entries(c.premise_changes)
    .map(([m, v]) => `${humanize(m)} ${v === null ? "n/a" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`}`)
    .join(", ");
  return `${w}${changes ? ` (${changes})` : ""}${c.holds ? "" : ": premise does not hold"}`;
}

/** Format of the nearest ancestor with a metric: a node's contribution effect is in its parent's units. */
export function parentFormatOf(tree: NormTree, node: NormNode): string | null {
  let pid = node.parent_id ?? null;
  while (pid) {
    const p = tree.nodes.get(pid);
    if (!p) return null;
    if (p.format) return p.format;
    pid = p.parent_id ?? null;
  }
  return null;
}
