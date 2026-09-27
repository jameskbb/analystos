/** Summarizes data-quality rule state per table for list views. Pure. */
import type { QualityRule } from "@/lib/api/types";

export interface DqSummary {
  active: number;
  failing: number;
  passing: number;
  neverRun: number;
  suggested: number;
  lastRunAt: string | null;
}

export function summarizeRules(rules: QualityRule[]): Map<string, DqSummary> {
  const out = new Map<string, DqSummary>();
  for (const r of rules) {
    const key = r.table_name;
    const s = out.get(key) ?? { active: 0, failing: 0, passing: 0, neverRun: 0, suggested: 0, lastRunAt: null };
    if (r.status === "suggested") s.suggested++;
    else if (r.status !== "disabled") {
      s.active++;
      if (r.last_passed === false) s.failing++;
      else if (r.last_passed === true) s.passing++;
      else s.neverRun++;
      if (r.last_run_at && (!s.lastRunAt || r.last_run_at > s.lastRunAt)) s.lastRunAt = r.last_run_at;
    }
    out.set(key, s);
  }
  return out;
}
