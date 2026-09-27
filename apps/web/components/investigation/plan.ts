/** Pure plan-editing operations used by the plan approval editor. */
import type { AnalysisPlan, PlanStep, PlanStepKind } from "@/lib/api/types";

export const STEP_KINDS: { kind: PlanStepKind; label: string; description: string }[] = [
  { kind: "decompose", label: "Decompose", description: "Split the change across metric-tree drivers (e.g. volume × price)." },
  { kind: "contribution", label: "Contribution", description: "Which segments of a dimension explain the change." },
  { kind: "segment", label: "Segment", description: "Compare the metric across segments of a dimension." },
  { kind: "compare", label: "Compare", description: "Compare periods, budget or forecast." },
  { kind: "anomaly", label: "Anomaly scan", description: "Look for unusual days or weeks in the window." },
  { kind: "custom_sql", label: "Custom SQL", description: "A read-only query you write yourself." },
  { kind: "python", label: "Python", description: "Sandboxed Python over query results." },
];

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
export function planProblems(plan: AnalysisPlan): string[] {
  const out: string[] = [];
  if (!enabledCount(plan)) out.push("Enable at least one step.");
  for (const s of plan.steps) {
    if (!s.enabled) continue;
    if (s.kind === "custom_sql" && !String(s.params.sql ?? "").trim()) out.push(`"${s.title}" needs SQL.`);
    if (s.kind === "python" && !String(s.params.code ?? "").trim()) out.push(`"${s.title}" needs Python code.`);
    if ((s.kind === "contribution" || s.kind === "segment") && !s.params.dimension && !s.params.dimensions)
      out.push(`"${s.title}" needs a dimension.`);
  }
  return out;
}
