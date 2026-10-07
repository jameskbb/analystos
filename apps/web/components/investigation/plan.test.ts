import { describe, expect, it } from "vitest";
import type { AnalysisPlan } from "@/lib/api/types";
import { addStep, moveStep, planProblems, premiseExpectation, removeStep, resolvedTerms, stepKindLabel, toggleStep, updateStep } from "./plan";

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

describe("premise and step wording", () => {
  it("says how each premise will be checked", () => {
    expect(premiseExpectation({ text: "revenue was roughly flat", term: "revenue", metric_id: "revenue", expectation: "flat", tolerance: 0.02 })).toBe(
      "Revenue within ±2%",
    );
    expect(
      premiseExpectation({ text: "we made our sales number", term: "revenue", metric_id: "revenue", expectation: "meets_plan", tolerance: 0.01, baseline_metric_id: "revenue_target" }),
    ).toBe("Revenue against Revenue Target, at most 1% short");
  });

  it("labels engine-planned steps", () => {
    expect(stepKindLabel("decompose")).toBe("Decompose");
    expect(stepKindLabel("reconcile")).toBe("Reconcile");
    expect(stepKindLabel("related_signals")).toBe("Related signals");
    expect(stepKindLabel("something_new")).toBe("Something New");
  });
});

describe("resolved terms", () => {
  it("lists words mapped through the glossary with the governed definition", () => {
    const interp = {
      metric_ids: ["operating_profit"],
      ambiguous: [],
      filters: [],
      intent: "why_change",
      resolved_terms: { "net profit": "operating_profit", revenue: "revenue" },
      confidence_notes: ["'net profit' resolved to 'operating_profit' (glossary term 'Net Profit')"],
    };
    const glossary = [{ id: "g1", term: "Net Profit", definition: "Operating Profit: interest and tax sit at corporate.", metric_id: "operating_profit" }];
    expect(resolvedTerms(interp, glossary)).toEqual([
      {
        phrase: "net profit",
        metricId: "operating_profit",
        via: "glossary term 'Net Profit'",
        glossaryTerm: "Net Profit",
        definition: "Operating Profit: interest and tax sit at corporate.",
      },
    ]);
    expect(resolvedTerms(null)).toEqual([]);
  });
});
