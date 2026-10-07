import { describe, expect, it } from "vitest";
import type { PlanVariance } from "@/lib/api/types";
import { planContradiction, planQuestion, planSentence, planStatusLabel, planTone } from "./plan";

function pv(over: Partial<PlanVariance>): PlanVariance {
  return {
    metric_id: "m",
    label: "Metric",
    format: "currency",
    plan_metric_id: "plan_m",
    plan_label: "Plan Metric",
    plan_role: "plan",
    period: "Q2 2026",
    window_start: "2026-04-01",
    window_end: "2026-07-01",
    actual: 100,
    plan: 100,
    variance: 0,
    variance_pct: 0,
    higher_is_better: true,
    status: "on_plan",
    tolerance: 0.02,
    sql_actual: "SELECT 1",
    sql_plan: "SELECT 1",
    ...over,
  };
}

const revenue = pv({ metric_id: "revenue", label: "Revenue", actual: 1003, plan: 1000, variance: 3, variance_pct: 0.003 });
const profit = pv({ metric_id: "operating_profit", label: "Operating Profit", actual: 53, plan: 100, variance: -47, variance_pct: -0.47, status: "unfavorable" });
const freight = pv({ metric_id: "freight_cost", label: "Freight", actual: 112, plan: 100, variance: 12, variance_pct: 0.12, status: "unfavorable", higher_is_better: false });

describe("planContradiction", () => {
  it("pairs the on-plan lead metric with the first unfavourable miss", () => {
    const c = planContradiction([revenue, profit, freight]);
    expect(c?.onPlan.metric_id).toBe("revenue");
    expect(c?.miss.metric_id).toBe("operating_profit");
  });

  it("is null when the lead metric itself missed or nothing missed", () => {
    expect(planContradiction([profit, freight])).toBeNull();
    expect(planContradiction([revenue, pv({ metric_id: "x", status: "favorable", variance: 5, variance_pct: 0.05 })])).toBeNull();
    expect(planContradiction([])).toBeNull();
    expect(planContradiction(undefined)).toBeNull();
  });
});

describe("plan wording", () => {
  it("words the contradiction from the measured values only", () => {
    const c = planContradiction([revenue, profit])!;
    expect(planSentence(c)).toBe("Revenue finished on plan in Q2 2026 (+0.3%), but Operating Profit missed plan by 47.0% (−$47).");
  });

  it("asks the stakeholder's question in the governed vocabulary's plain words", () => {
    const words = planContradiction([{ ...revenue, stakeholder_term: "sales" }, { ...profit, stakeholder_term: "net profit" }])!;
    expect(planQuestion(words)).toBe("We made our sales number last quarter, but we missed our target for net profit. What happened?");
    // Without a stakeholder word, the label stands in.
    expect(planQuestion(planContradiction([revenue, profit])!, "in Q2 2026")).toBe(
      "We made our revenue number in Q2 2026, but we missed our target for operating profit. What happened?",
    );
  });

  it("labels costs over plan as unfavourable and revenue on plan as positive", () => {
    expect(planStatusLabel(freight)).toBe("Over plan");
    expect(planTone(freight)).toBe("negative");
    expect(planStatusLabel(revenue)).toBe("On plan");
    expect(planTone(revenue)).toBe("positive");
    expect(planStatusLabel(profit)).toBe("Below plan");
  });
});
