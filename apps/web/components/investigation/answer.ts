/**
 * Pure helpers for the parts of an investigation an executive reads first: the premise verdicts,
 * the headline gap, whether the explanation reconciles, and the related signals kept apart from
 * the explanation. They only select and label values the engine measured; none computes a number.
 */
import type { RelatedSignal } from "@/lib/api/types";
import type { NormNode, NormTree } from "./model";
import { premiseVerdict } from "./model";

/** A related signal: verified, but its relationship to the explained change is not established. */
export function isRelatedSignal(n: Pick<NormNode, "role" | "relationship">): boolean {
  return n.role === "related_signal" || n.relationship === "not_established";
}

export function premiseNodes(tree: NormTree): NormNode[] {
  return [...tree.nodes.values()].filter((n) => n.role === "premise" || premiseVerdict(n) !== null);
}

export function reconciliationNode(tree: NormTree): NormNode | null {
  return [...tree.nodes.values()].find((n) => !!n.reconciliation) ?? null;
}

/** Related-signal nodes in tree order (top-level signals only; their supporting children stay beneath them). */
export function relatedSignalNodes(tree: NormTree): NormNode[] {
  const all = [...tree.nodes.values()].filter(isRelatedSignal);
  const ids = new Set(all.map((n) => n.id));
  return all.filter((n) => !n.parent_id || !ids.has(n.parent_id));
}

/**
 * The explanation tree without its related signals: they are removed from their parents' child lists
 * (so the tree shows only what reconciles) but stay in the node map, so selecting one still opens
 * its detail, evidence and SQL.
 */
export function explanationTree(tree: NormTree): NormTree {
  const signals = new Set(relatedSignalNodes(tree).map((n) => n.id));
  const recon = reconciliationNode(tree)?.reconciliation;
  if (!signals.size && !recon) return tree;
  const nodes = new Map<string, NormNode>();
  tree.nodes.forEach((n, id) => nodes.set(id, { ...n, children: n.children.filter((c) => !signals.has(c)) }));
  const root = tree.rootId ? nodes.get(tree.rootId) : undefined;
  if (root && recon) {
    // Read the root's children in the order an executive needs them: the reconciled parts of the gap,
    // then the premise and the reconciliation check, then other (overlapping) views and hypotheses.
    const parts = recon.identities[0]?.components.map((c) => c.node_id).filter((id): id is string => !!id) ?? [];
    const rank = (id: string): number => {
      const n = nodes.get(id);
      const i = parts.indexOf(id);
      if (i >= 0) return i;
      if (n?.role === "premise") return 100;
      if (n?.role === "reconciliation") return 101;
      if (n?.statement_type === "hypothesis") return 300;
      return 200;
    };
    root.children = [...root.children].sort((a, b) => rank(a) - rank(b));
  }
  return { rootId: tree.rootId, nodes };
}

/**
 * Breakdowns of the root's gap by a dimension, next to a reconciled explanation: a second view of the same
 * gap that overlaps the explanation's parts, so it is shown collapsed and labelled, never summed with them.
 */
export function overlappingViewIds(tree: NormTree): Set<string> {
  const root = tree.rootId ? tree.nodes.get(tree.rootId) : undefined;
  if (!root || !reconciliationNode(tree)) return new Set();
  return new Set(root.children.filter((c) => tree.nodes.get(c)?.kind === "dimension"));
}

/** The Investigation-level record for a related-signal node (carries `provisional`), if any. */
export function signalRecord(signals: RelatedSignal[] | null | undefined, nodeId: string): RelatedSignal | null {
  return signals?.find((s) => s.node_id === nodeId) ?? null;
}

/** Provisional signals are measured on cohorts that have not finished maturing; say so in plain words. */
export const PROVISIONAL_NOTE =
  "Provisional: the most recent cohorts are younger than this metric's maturity window, so the value can still move as they mature. Its evidence is rated weak for that reason, not because the query is in doubt.";

/** "Median First Response (hours)" with unit "hours" reads "Median First Response". */
export function labelWithoutUnit(label: string | null | undefined, unit: string | null | undefined): string {
  if (!label) return "";
  if (!unit) return label;
  return label.replace(new RegExp(`\\s*\\(${unit.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\)\\s*$`, "i"), "");
}

const PLAN_WORD = /\b(plan|budget|target|forecast|quota)\b/i;

/** True when the node's baseline is a plan, budget, target or forecast rather than an earlier period. */
export function isPlanBaseline(n: Pick<NormNode, "baseline_label">): boolean {
  return !!n.baseline_label && PLAN_WORD.test(n.baseline_label);
}

/** Column labels for a node's values: Actual / Plan / Variance against a plan, else Current / Baseline / Change. */
export function valueLabels(n: Pick<NormNode, "baseline_label">): { current: string; baseline: string; change: string } {
  if (isPlanBaseline(n)) return { current: "Actual", baseline: n.baseline_label ?? "Plan", change: "Variance" };
  return { current: "Current", baseline: n.baseline_label || "Baseline", change: "Change" };
}

/** "Operating Profit miss" for a shortfall against plan, otherwise "Operating Profit change". */
export function gapPhrase(root: Pick<NormNode, "metric_label" | "metric_id" | "baseline_label" | "abs_change"> | null | undefined): string {
  const label = root?.metric_label ?? root?.metric_id ?? "explained";
  const miss = !!root && isPlanBaseline(root) && (root.abs_change ?? 0) < 0;
  return `${label} ${miss ? "miss" : "change"}`;
}

/** The line every related signal carries: it is not claimed as a cause of the explained change. */
export function relationshipLabel(root: Parameters<typeof gapPhrase>[0]): string {
  return `Relationship to the ${gapPhrase(root)}: not established`;
}

/**
 * Colour a node's own change by what it did to the root: a cost that rose (+) while lowering profit
 * (root_effect < 0) reads as unfavourable. Without a root effect, higher is better.
 */
export function higherIsBetterFor(n: Pick<NormNode, "abs_change" | "pct_change" | "root_effect">): boolean {
  const change = n.abs_change ?? n.pct_change;
  if (!change || !n.root_effect) return true;
  return Math.sign(change) === Math.sign(n.root_effect);
}

/** "Other lines, net": immaterial lines the engine grouped into one reconciling node (it lists them in `members`). */
export function isOtherLines(n: Pick<NormNode, "members" | "title">): boolean {
  return !!n.members?.length || n.title === "Other lines, net";
}

/** A node counts as material in the default view when its effect is at least this share of the root gap. */
export const STORY_MIN_SHARE = 0.05;
/** A breakdown under a material node opens when one segment carries at least this share of that node's effect. */
export const STORY_CONCENTRATION = 0.25;

/**
 * Default expansion for a decomposed gap: open the path from the root to the operational causes, so the
 * first screen tells the story without clicking. A node opens when its effect on the root is at least
 * STORY_MIN_SHARE of the root gap; a breakdown group under an open node opens when one of its segments is
 * itself material or carries STORY_CONCENTRATION of the parent's effect. Premises, the reconciliation,
 * related signals, other-lines, hypotheses and overlapping views stay closed. Returns null when the tree
 * has no root effects (not a decomposed gap), so the caller keeps its usual default.
 */
export function storyExpansion(tree: NormTree): Set<string> | null {
  const root = tree.rootId ? tree.nodes.get(tree.rootId) : undefined;
  const gap = Math.abs(root?.root_effect ?? root?.abs_change ?? 0);
  if (!root || !gap || ![...tree.nodes.values()].some((n) => n.id !== root.id && n.root_effect != null)) return null;
  const overlapping = overlappingViewIds(tree);
  const open = new Set<string>([root.id]);
  const effect = (id: string) => Math.abs(tree.nodes.get(id)?.root_effect ?? 0);
  const skip = (n: NormNode) =>
    overlapping.has(n.id) || isRelatedSignal(n) || isOtherLines(n) || !!n.role || n.statement_type === "hypothesis";
  const visit = (id: string, parentEffect: number) => {
    const n = tree.nodes.get(id);
    if (!n || skip(n) || !n.children.length) return;
    const kids = n.children.map((c) => tree.nodes.get(c)).filter((c): c is NormNode => !!c);
    const isGroup = n.kind === "dimension" || n.root_effect == null;
    const material = isGroup
      ? kids.some((k) => effect(k.id) >= STORY_MIN_SHARE * gap || (parentEffect > 0 && effect(k.id) >= STORY_CONCENTRATION * parentEffect))
      : effect(n.id) >= STORY_MIN_SHARE * gap;
    if (!material) return;
    open.add(n.id);
    for (const k of kids) visit(k.id, isGroup ? parentEffect : effect(n.id));
  };
  for (const c of root.children) visit(c, gap);
  return open;
}
