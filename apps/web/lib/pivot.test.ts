import { describe, expect, it } from "vitest";
import { computePivot, partialAggregatesFor, partialCol } from "./pivot";

const raw = {
  columns: ["region", "channel", "customer", "revenue"],
  rows: [
    ["Dallas", "Web", "c1", 100],
    ["Dallas", "Web", "c2", 50],
    ["Dallas", "Branch", "c1", 30],
    ["Houston", "Web", "c3", 20],
    ["Houston", "Branch", "c3", 80],
    ["Houston", "Branch", "c4", null],
  ],
};

describe("computePivot (raw)", () => {
  it("computes cells, row totals and a grand total", () => {
    const p = computePivot(raw, { rows: ["region"], cols: ["channel"], values: [{ field: "revenue", agg: "sum" }] });
    expect(p.colKeys).toEqual([["Branch"], ["Web"]]);
    const labels = p.rowHeaders.map((r) => r.label);
    expect(labels).toEqual(["Dallas", "Houston", "Grand total"]);
    // [Branch, Web, Total]
    expect(p.values[0].map((c) => c[0])).toEqual([30, 150, 180]);
    expect(p.values[1].map((c) => c[0])).toEqual([80, 20, 100]);
    expect(p.values[2].map((c) => c[0])).toEqual([110, 170, 280]);
  });

  it("adds subtotals for nested row dimensions", () => {
    const p = computePivot(raw, { rows: ["region", "channel"], cols: [], values: [{ field: "revenue", agg: "sum" }] });
    expect(p.rowHeaders.map((r) => `${r.kind}:${r.label}`)).toEqual([
      "leaf:Branch",
      "leaf:Web",
      "subtotal:Dallas total",
      "leaf:Branch",
      "leaf:Web",
      "subtotal:Houston total",
      "grand:Grand total",
    ]);
    expect(p.values[2][0][0]).toBe(180);
    expect(p.values[6][0][0]).toBe(280);
  });

  it("supports avg, count and exact distinct counts, ignoring nulls", () => {
    const p = computePivot(raw, {
      rows: ["region"],
      cols: [],
      values: [
        { field: "revenue", agg: "avg" },
        { field: "revenue", agg: "count" },
        { field: "customer", agg: "count_distinct" },
      ],
    });
    expect(p.values[1][0]).toEqual([50, 2, 2]); // Houston: (20+80)/2, 2 non-null, c3,c4
    expect(p.values[2][0][2]).toBe(4); // grand distinct customers
  });

  it("applies include/exclude filters and sorts by value", () => {
    const p = computePivot(raw, {
      rows: ["region"],
      cols: [],
      values: [{ field: "revenue", agg: "sum" }],
      filters: [{ field: "channel", values: ["Web"] }],
      sort: { by: "value", direction: "asc" },
    });
    expect(p.rowHeaders.map((r) => r.label)).toEqual(["Houston", "Dallas", "Grand total"]);
    expect(p.values[0][0][0]).toBe(20);
    const ex = computePivot(raw, {
      rows: ["region"],
      cols: [],
      values: [{ field: "revenue", agg: "sum" }],
      filters: [{ field: "region", values: ["Dallas"], exclude: true }],
    });
    expect(ex.rowHeaders.map((r) => r.label)).toEqual(["Houston", "Grand total"]);
  });

  it("can hide subtotals and grand totals", () => {
    const p = computePivot(raw, {
      rows: ["region", "channel"],
      cols: ["customer"],
      values: [{ field: "revenue", agg: "sum" }],
      subtotals: false,
      grandTotals: false,
    });
    expect(p.rowHeaders.every((r) => r.kind === "leaf")).toBe(true);
    expect(p.hasRowTotalColumn).toBe(false);
    expect(p.values[0].length).toBe(p.colKeys.length);
  });
});

describe("computePivot (partial aggregates)", () => {
  it("merges server partials exactly and blanks non-additive distinct subtotals", () => {
    const values = [
      { field: "revenue", agg: "avg" as const },
      { field: "customer", agg: "count_distinct" as const },
    ];
    const aggs = partialAggregatesFor(values);
    expect(aggs.map((a) => a.alias)).toEqual([partialCol("sum", "revenue"), partialCol("count", "revenue"), partialCol("cd", "customer")]);
    const input = {
      mode: "partial" as const,
      columns: ["region", "channel", ...aggs.map((a) => a.alias)],
      rows: [
        ["Dallas", "Web", 150, 2, 2],
        ["Dallas", "Branch", 30, 1, 1],
      ],
    };
    const p = computePivot(input, { rows: ["region", "channel"], cols: [], values });
    const sub = p.rowHeaders.findIndex((r) => r.kind === "subtotal");
    expect(p.values[sub][0][0]).toBe(60); // (150+30)/3
    expect(p.values[sub][0][1]).toBeNull();
    expect(p.values[0][0][1]).toBe(1);
    expect(p.nonAdditiveNote).toMatch(/not additive/);
  });
});
