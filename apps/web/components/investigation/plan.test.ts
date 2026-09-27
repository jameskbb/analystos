import { describe, expect, it } from "vitest";
import type { AnalysisPlan } from "@/lib/api/types";
import { addStep, moveStep, planProblems, removeStep, toggleStep, updateStep } from "./plan";

const plan: AnalysisPlan = {
  requires_approval: true,
  steps: [
    { id: "s1", kind: "decompose", title: "Revenue components", rationale: "", params: {}, enabled: true },
    { id: "s2", kind: "contribution", title: "Geographic mix", rationale: "", params: { dimension: "branch" }, enabled: true },
  ],
};

describe("plan editing", () => {
  it("toggles, removes and reorders steps immutably", () => {
    const t = toggleStep(plan, "s1");
    expect(t.steps[0].enabled).toBe(false);
    expect(plan.steps[0].enabled).toBe(true);
    expect(removeStep(plan, "s1").steps.map((s) => s.id)).toEqual(["s2"]);
    expect(moveStep(plan, "s2", -1).steps.map((s) => s.id)).toEqual(["s2", "s1"]);
    expect(moveStep(plan, "s1", -1)).toBe(plan);
  });

  it("adds user steps with unique ids and merges params on update", () => {
    const a = addStep(addStep(plan, "contribution", { dimension: "channel" }), "contribution", { dimension: "segment" });
    expect(a.steps.slice(2).map((s) => s.id)).toEqual(["user-contribution-1", "user-contribution-2"]);
    expect(a.steps[2]).toMatchObject({ title: "Contribution by channel", origin: "user", enabled: true });
    const u = updateStep(plan, "s2", { params: { top_n: 3 } });
    expect(u.steps[1].params).toEqual({ dimension: "branch", top_n: 3 });
  });

  it("reports problems that would make the plan unrunnable", () => {
    expect(planProblems(plan)).toEqual([]);
    expect(planProblems(toggleStep(toggleStep(plan, "s1"), "s2"))).toEqual(["Enable at least one step."]);
    const withSql = addStep(plan, "custom_sql");
    expect(planProblems(withSql)[0]).toMatch(/needs SQL/);
    expect(planProblems(addStep(plan, "contribution"))[0]).toMatch(/needs a dimension/);
  });
});
