/** Pure plan-editing operations used by the plan approval editor. */
import type { AnalysisPlan, GlossaryTerm, Interpretation, PlanStep, PlanStepKind, Premise } from "@/lib/api/types";
import { humanize } from "@/lib/format";

export const STEP_KINDS: { kind: PlanStepKind; label: string; description: string }[] = [
  { kind: "decompose", label: "Decompose", description: "Split the change across metric-tree drivers (e.g. volume × price)." },
  { kind: "contribution", label: "Contribution", description: "Which segments of a dimension explain the change." },
  { kind: "segment", label: "Segment", description: "Compare the metric across segments of a dimension." },
  { kind: "compare", label: "Compare", description: "Compare periods, budget or forecast." },
  { kind: "anomaly", label: "Anomaly scan", description: "Look for unusual days or weeks in the window." },
  { kind: "custom_sql", label: "Custom SQL", description: "A read-only query you write yourself." },
  { kind: "python", label: "Python", description: "Sandboxed Python over query results." },
];

/** Steps the engine plans itself but a user does not add by hand. */
const ENGINE_STEP_LABEL: Record<string, string> = {
  reconcile: "Reconcile",
  related_signals: "Related signals",
};

export function stepKindLabel(kind: string): string {
  return STEP_KINDS.find((k) => k.kind === kind)?.label ?? ENGINE_STEP_LABEL[kind] ?? humanize(kind);
}

/** How a premise will be checked: "Revenue within ±2%", "Revenue against Plan Revenue, at most 1% short". */
export function premiseExpectation(p: Premise): string {
  const metric = humanize(p.metric_id ?? p.term);
  const tol = `${+((p.tolerance ?? 0.02) * 100).toFixed(1)}%`;
  if (p.expectation === "flat") return `${metric} within ±${tol}`;
  if (p.expectation === "meets_plan") return `${metric} against ${humanize(p.baseline_metric_id ?? "plan")}, at most ${tol} short`;
  if (p.expectation === "misses_plan")
    return `${metric} against ${humanize(p.baseline_metric_id ?? "plan")}, ${(p.tolerance ?? 0) > 0 ? `more than ${tol} short` : "below it"}`;
  return `${metric} ${p.expectation}`;
}

export function toggleStep(plan: AnalysisPlan, id: string): AnalysisPlan {
  return { ...plan, steps: plan.steps.map((s) => (s.id === id ? { ...s, enabled: !s.enabled } : s)) };
}

export function removeStep(plan: AnalysisPlan, id: string): AnalysisPlan {
  return { ...plan, steps: plan.steps.filter((s) => s.id !== id) };
}

export function moveStep(plan: AnalysisPlan, id: string, delta: -1 | 1): AnalysisPlan {
  const i = plan.steps.findIndex((s) => s.id === id);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= plan.steps.length) return plan;
  const steps = [...plan.steps];
  [steps[i], steps[j]] = [steps[j], steps[i]];
  return { ...plan, steps };
}

export function updateStep(plan: AnalysisPlan, id: string, patch: Partial<PlanStep>): AnalysisPlan {
  return { ...plan, steps: plan.steps.map((s) => (s.id === id ? { ...s, ...patch, params: { ...s.params, ...(patch.params ?? {}) } } : s)) };
}

export function addStep(plan: AnalysisPlan, kind: PlanStepKind, params: Record<string, unknown> = {}, title?: string): AnalysisPlan {
  const meta = STEP_KINDS.find((k) => k.kind === kind);
  const base = `user-${kind}`;
  let n = 1;
  while (plan.steps.some((s) => s.id === `${base}-${n}`)) n++;
  const dim = typeof params.dimension === "string" ? params.dimension : null;
  const step: PlanStep = {
    id: `${base}-${n}`,
    kind,
    title: title ?? (dim ? `${meta?.label ?? kind} by ${dim}` : meta?.label ?? kind),
    rationale: "Added by the analyst.",
    params,
    enabled: true,
    origin: "user",
  };
  return { ...plan, steps: [...plan.steps, step] };
}

export function enabledCount(plan: AnalysisPlan): number {
  return plan.steps.filter((s) => s.enabled).length;
}

/** Plan is runnable when at least one step is enabled and custom steps carry their code. */
/** A variance-drill step: the engine chooses the governed dimensions per material variance, so none is set by hand. */
export function enginePicksDimensions(s: PlanStep): boolean {
  return s.params.role === "variance_drill";
}

export function planProblems(plan: AnalysisPlan): string[] {
  const out: string[] = [];
  if (!enabledCount(plan)) out.push("Enable at least one step.");
  for (const s of plan.steps) {
    if (!s.enabled) continue;
    if (s.kind === "custom_sql" && !String(s.params.sql ?? "").trim()) out.push(`"${s.title}" needs SQL.`);
    if (s.kind === "python" && !String(s.params.code ?? "").trim()) out.push(`"${s.title}" needs Python code.`);
    if ((s.kind === "contribution" || s.kind === "segment") && !s.params.dimension && !s.params.dimensions && !enginePicksDimensions(s))
      out.push(`"${s.title}" needs a dimension.`);
  }
  return out;
}

export interface ResolvedTerm {
  /** The words in the question ("net profit"). */
  phrase: string;
  metricId: string;
  /** How the interpreter matched it, from its confidence note ("glossary term 'Net Profit'"), if it said. */
  via: string | null;
  /** The governed definition behind the match, when the glossary holds one. */
  glossaryTerm: string | null;
  definition: string | null;
}

const RESOLVED_NOTE = /^'(.+?)' resolved to '(.+?)' \((.+)\)$/;

/**
 * The words of the question the interpreter mapped onto governed metrics, with how each was matched and
 * the glossary definition behind it: the moment the question's vocabulary meets the semantic model.
 * Only terms whose words differ from the metric id are listed (a literal "revenue" needs no explaining).
 */
export function resolvedTerms(interp: Interpretation | null | undefined, glossary: GlossaryTerm[] = []): ResolvedTerm[] {
  const notes = new Map<string, string>();
  for (const n of interp?.confidence_notes ?? []) {
    const m = RESOLVED_NOTE.exec(n);
    if (m) notes.set(m[1].toLowerCase(), m[3]);
  }
  return Object.entries(interp?.resolved_terms ?? {})
    .filter(([phrase, id]) => phrase.toLowerCase().replace(/\s+/g, "_") !== id)
    .map(([phrase, metricId]) => {
      const via = notes.get(phrase.toLowerCase()) ?? null;
      const named = via ? /glossary term '(.+)'/.exec(via)?.[1] ?? null : null;
      const norm = phrase.toLowerCase();
      const g =
        glossary.find((t) => named && t.term.toLowerCase() === named.toLowerCase()) ??
        glossary.find((t) => t.metric_id === metricId && (t.term.toLowerCase() === norm || (t.synonyms ?? []).some((x) => x.toLowerCase() === norm))) ??
        null;
      return { phrase, metricId, via, glossaryTerm: g?.term ?? named, definition: g?.definition ?? null };
    });
}
