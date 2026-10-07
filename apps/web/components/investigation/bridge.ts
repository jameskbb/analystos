/**
 * The plan-to-actual bridge: the root metric's plan (or baseline) value, each reconciled effect, then
 * the actual value. These helpers only read values the engine measured and lay them out; the one sum
 * they do (the running total between steps) is the same arithmetic the reconciliation already checked,
 * and `bridgeGap` reports any difference instead of hiding it.
 */
import type { Bridge, BridgeStep, Investigation, Reconciliation } from "@/lib/api/types";
import type { NormTree } from "./model";
import { reconciliationNode } from "./answer";

/**
 * The investigation's bridge: the engine's own (`Investigation.bridge`, or on the root node) when it
 * publishes one, otherwise one read off the reconciliation identities (each top-level part replaced by
 * its own reconciled parts, so the steps are the leaves of the reconciled explanation).
 */
export function bridgeOf(inv: Pick<Investigation, "bridge"> | null | undefined, tree: NormTree): Bridge | null {
  if (inv?.bridge?.steps?.length) return inv.bridge;
  const root = tree.rootId ? tree.nodes.get(tree.rootId) : undefined;
  if (root?.bridge?.steps?.length) return root.bridge;
  const recon = reconciliationNode(tree)?.reconciliation;
  return recon ? bridgeFromReconciliation(recon) : null;
}

export function bridgeFromReconciliation(r: Reconciliation): Bridge | null {
  const top = r.identities[0];
  if (!top || !r.reconciled) return null;
  const byParent = new Map(r.identities.slice(1).filter((i) => i.parent_node_id).map((i) => [i.parent_node_id!, i]));
  const steps: BridgeStep[] = [];
  let residual = top.residual;
  for (const c of top.components) {
    const sub = c.node_id ? byParent.get(c.node_id) : undefined;
    if (sub && sub.reconciled && sub.components.length) {
      residual += sub.residual;
      for (const s of sub.components) steps.push({ node_id: s.node_id, label: s.label, amount: s.amount, group_label: c.label, group_node_id: c.node_id });
    } else {
      steps.push({ node_id: c.node_id, label: c.label, amount: c.amount, group_label: c.label, group_node_id: c.node_id });
    }
  }
  return {
    metric_id: r.metric_id,
    metric_label: r.metric_label,
    metric_format: r.metric_format,
    start_label: "Plan",
    start: r.baseline,
    end_label: "Actual",
    end: r.actual,
    steps,
    residual,
    reconciled: r.reconciled,
  };
}

export interface BridgeBar {
  kind: "total" | "step";
  label: string;
  node_id?: string | null;
  group?: string | null;
  /** The signed amount the bar shows (the total itself for totals). */
  amount: number;
  /** Bar extent on the value axis. */
  from: number;
  to: number;
}

/** Plan total, one floating bar per step (from the running total before it to the one after), actual total. */
export function bridgeBars(b: Bridge): BridgeBar[] {
  const bars: BridgeBar[] = [{ kind: "total", label: b.start_label, amount: b.start, from: 0, to: b.start }];
  let run = b.start;
  for (const s of b.steps) {
    // The engine's running total when it publishes one; otherwise the same running sum.
    const to = s.running_total ?? run + s.amount;
    bars.push({ kind: "step", label: s.label, node_id: s.node_id, group: s.group_label, amount: s.amount, from: to - s.amount, to });
    run = to;
  }
  bars.push({ kind: "total", label: b.end_label, amount: b.end, from: 0, to: b.end });
  return bars;
}

/** How far the steps miss the actual (0 when the bridge is exact). */
export function bridgeGap(b: Bridge): number {
  return b.end - (b.start + b.steps.reduce((s, x) => s + x.amount, 0) + (b.residual ?? 0));
}

/** Runs of consecutive steps that share a group, for the group labels under the bars. */
export function bridgeGroups(b: Bridge): { group: string; group_node_id?: string | null; first: number; last: number }[] {
  const out: { group: string; group_node_id?: string | null; first: number; last: number }[] = [];
  b.steps.forEach((s, i) => {
    const g = s.group_label ?? "";
    const prev = out[out.length - 1];
    if (prev && prev.group === g) prev.last = i;
    else out.push({ group: g, group_node_id: s.group_node_id, first: i, last: i });
  });
  return out.filter((g) => g.group);
}
