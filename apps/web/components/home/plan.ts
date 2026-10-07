/**
 * Pure helpers for the home "Against plan" panel. Every value comes from `GET /home`
 * (`plan_variances`, each side an executed semantic query); nothing here computes a number,
 * it only chooses which measured values to put side by side and words them.
 */
import type { PlanVariance } from "@/lib/api/types";
import { formatPctChange, formatSignedChange } from "@/lib/format";

export interface PlanContradiction {
  /** The headline metric that landed on plan (the canonical revenue metric when it has a plan). */
  onPlan: PlanVariance;
  /** The first headline metric after it that missed plan unfavourably. */
  miss: PlanVariance;
}

/**
 * The contradiction worth investigating: the first metric (the API orders revenue, then headline
 * metrics, then their tree lines) landed on plan while a later headline metric missed plan.
 * Returns null when there is no such pair; the panel then shows the tiles only.
 */
export function planContradiction(items: PlanVariance[] | null | undefined): PlanContradiction | null {
  if (!items?.length) return null;
  const [first, ...rest] = items;
  if (first.status !== "on_plan") return null;
  const miss = rest.find((v) => v.status === "unfavorable" && v.variance !== null);
  return miss ? { onPlan: first, miss } : null;
}

/** Status wording for a tile: on plan, or above/below (over/under for costs) plan. */
export function planStatusLabel(v: PlanVariance): string {
  if (v.status === "on_plan") return "On plan";
  const up = (v.variance ?? 0) > 0;
  if (v.higher_is_better) return up ? "Above plan" : "Below plan";
  return up ? "Over plan" : "Under plan";
}

export function planTone(v: PlanVariance): "positive" | "negative" | "neutral" {
  if (v.status === "unfavorable") return "negative";
  if (v.status === "on_plan" || v.status === "favorable") return "positive";
  return "neutral";
}

/** "Revenue finished on plan in Q2 2026 (+0.3%), but Operating Profit missed plan by 47.2% (−$1.9M)." */
export function planSentence(c: PlanContradiction): string {
  const { onPlan, miss } = c;
  const onPct = onPlan.variance_pct === null ? "" : ` (${formatPctChange(onPlan.variance_pct)})`;
  const missPct = miss.variance_pct === null ? "" : ` by ${formatPctChange(Math.abs(miss.variance_pct)).replace(/^\+/, "")}`;
  const missAbs = miss.variance === null ? "" : ` (${formatSignedChange(miss.variance, miss.format)})`;
  return `${onPlan.label} finished on plan in ${onPlan.period}${onPct}, but ${miss.label} missed plan${missPct}${missAbs}.`;
}

/**
 * The question the "Investigate why" button asks, in a stakeholder's words. Each metric is named by its
 * `stakeholder_term` (from the glossary and synonyms, see GET /home) rather than its label, so the
 * sentence reads like the one an executive sends ("sales", "net profit"); the investigation resolves
 * those words back through the same governed vocabulary, verifies the premise and measures the gap.
 * `periodPhrase` is how the question refers to the API's window: the home endpoint compares the latest
 * closed quarter, which is what "last quarter" resolves to.
 */
export function planQuestion(c: PlanContradiction, periodPhrase = "last quarter"): string {
  const word = (v: PlanVariance) => v.stakeholder_term || v.label.toLowerCase();
  return `We made our ${word(c.onPlan)} number ${periodPhrase}, but we missed our target for ${word(c.miss)}. What happened?`;
}
