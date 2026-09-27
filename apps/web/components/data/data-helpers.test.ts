import { describe, expect, it } from "vitest";
import { suggestTableName, TABLE_NAME_RE } from "./naming";
import { summarizeRules } from "./dq-summary";
import { isManyToMany, readJoinAnalysis } from "./relationships";
import { withRefs } from "./dataset-tabs";
import { sortIssues } from "./profile-view";
import type { QualityRule, Relationship } from "@/lib/api/types";

describe("naming", () => {
  it("suggests SQL-safe table names", () => {
    expect(suggestTableName("Budgets 2026 (FY).xlsx")).toBe("budgets_2026_fy");
    expect(suggestTableName("2026 targets.csv")).toBe("t_2026_targets");
    expect(TABLE_NAME_RE.test("order_lines")).toBe(true);
    expect(TABLE_NAME_RE.test("Order Lines")).toBe(false);
  });
});

describe("summarizeRules", () => {
  it("counts failing, passing, never-run and suggested rules per table", () => {
    const r = (p: Partial<QualityRule>): QualityRule =>
      ({ id: Math.random().toString(), table_name: "orders", name: "r", kind: "not_null", params: {}, severity: "warning", origin: "manual", status: "active", ...p }) as QualityRule;
    const s = summarizeRules([
      r({ last_passed: false, last_run_at: "2026-09-01T00:00:00Z" }),
      r({ last_passed: true, last_run_at: "2026-09-02T00:00:00Z" }),
      r({}),
      r({ status: "suggested" }),
      r({ status: "disabled", last_passed: false }),
      r({ table_name: "customers", last_passed: true }),
    ]);
    expect(s.get("orders")).toEqual({ active: 3, failing: 1, passing: 1, neverRun: 1, suggested: 1, lastRunAt: "2026-09-02T00:00:00Z" });
    expect(s.get("customers")?.passing).toBe(1);
  });
});

describe("join analysis", () => {
  it("normalizes analysis dicts and flags many-to-many", () => {
    const ja = readJoinAnalysis({ observed_cardinality: "many_to_many", fanout_factor: 3.2, orphan_pct: 0.01, warnings: ["fan-out"] });
    expect(ja).toMatchObject({ fanout_factor: 3.2, warnings: ["fan-out"] });
    const rel = { cardinality: "many_to_one", join_analysis: { observed_cardinality: "many_to_many" } } as unknown as Relationship;
    expect(isManyToMany(rel)).toBe(true);
    expect(isManyToMany({ cardinality: "one_to_many", join_analysis: null } as unknown as Relationship)).toBe(false);
  });
});

describe("lineage refs", () => {
  it("derives navigation refs from prefixed node ids", () => {
    const g = withRefs({
      nodes: [
        { id: "metric:revenue", kind: "metric", label: "revenue" },
        { id: "dataset:abc", kind: "dataset", label: "Orders" },
        { id: "entity:order", kind: "entity", label: "order" },
      ],
      edges: [],
    });
    expect(g.nodes.map((n) => n.ref_id ?? null)).toEqual(["revenue", "abc", null]);
  });
});

describe("profile issues", () => {
  it("sorts errors first, then by affected rows", () => {
    const s = sortIssues([
      { code: "outliers", severity: "info", message: "", count: 100 },
      { code: "duplicate_ids", severity: "error", message: "", count: 2 },
      { code: "unexpected_nulls", severity: "warning", message: "", count: 5 },
      { code: "mixed_types", severity: "warning", message: "", count: 50 },
    ]);
    expect(s.map((i) => i.code)).toEqual(["duplicate_ids", "mixed_types", "unexpected_nulls", "outliers"]);
  });
});
