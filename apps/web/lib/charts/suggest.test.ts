import { describe, expect, it } from "vitest";
import { compatibleTypes, profileFields, suggestChart, suggestCharts } from "./suggest";

const cols = (...spec: [string, string][]) => spec.map(([name, type]) => ({ name, type }));

describe("profileFields", () => {
  it("classifies measures, temporal, categorical and identifiers", () => {
    const f = profileFields(
      cols(["order_id", "BIGINT"], ["order_date", "DATE"], ["region", "VARCHAR"], ["revenue", "DOUBLE"]),
      [
        [1, "2026-08-01", "Dallas", 10.5],
        [2, "2026-08-02", "Houston", 12],
        [3, "2026-08-03", "Dallas", 9],
      ],
    );
    expect(f.map((x) => x.role)).toEqual(["identifier", "temporal", "categorical", "measure"]);
  });

  it("detects ISO date strings without a declared temporal type", () => {
    const [f] = profileFields(cols(["month", "VARCHAR"]), [["2026-07"], ["2026-08"]]);
    expect(f.role).toBe("temporal");
  });
});

describe("suggestCharts", () => {
  it("suggests a KPI for a single row of measures", () => {
    expect(suggestChart(cols(["revenue", "DOUBLE"], ["orders", "BIGINT"]), [[4200000, 1234]]).type).toBe("kpi");
  });

  it("suggests a line chart for a time series", () => {
    const rows = Array.from({ length: 12 }, (_, i) => [`2026-${String(i + 1).padStart(2, "0")}-01`, 100 + i]);
    const s = suggestCharts(cols(["month", "DATE"], ["revenue", "DOUBLE"]), rows);
    expect(s[0].config).toMatchObject({ type: "line", x: "month", y: ["revenue"] });
    expect(s.map((x) => x.config.type)).toContain("area");
  });

  it("splits a time series by a low-cardinality category", () => {
    const rows: unknown[][] = [];
    for (const m of ["2026-06-01", "2026-07-01", "2026-08-01"])
      for (const r of ["Dallas", "Houston", "Austin"]) rows.push([m, r, Math.random() * 100]);
    const top = suggestChart(cols(["month", "DATE"], ["region", "VARCHAR"], ["revenue", "DOUBLE"]), rows);
    expect(top).toMatchObject({ type: "line", series: "region" });
  });

  it("suggests a sorted bar for category × measure, horizontal when there are many categories", () => {
    const few = suggestChart(cols(["region", "VARCHAR"], ["revenue", "DOUBLE"]), [
      ["Dallas", 5],
      ["Houston", 3],
      ["Austin", 4],
    ]);
    expect(few).toMatchObject({ type: "bar", x: "region", sort: { field: "revenue", direction: "desc" }, horizontal: false });
    const many = suggestChart(
      cols(["sku", "VARCHAR"], ["revenue", "DOUBLE"]),
      Array.from({ length: 20 }, (_, i) => [`P${i}`, i]),
    );
    expect(many).toMatchObject({ type: "bar", horizontal: true });
  });

  it("suggests a waterfall for signed changes by segment", () => {
    const s = suggestCharts(cols(["driver", "VARCHAR"], ["revenue_change", "DOUBLE"]), [
      ["Dallas", -420000],
      ["Enterprise", -300000],
      ["Roofing", 120000],
    ]);
    expect(s[0].config.type).toBe("waterfall");
  });

  it("suggests a heatmap for two categories and a measure", () => {
    const rows: unknown[][] = [];
    for (const r of ["A", "B", "C", "D", "E", "F", "G"]) for (const c of ["x", "y", "z", "w", "v"]) rows.push([r, c, 1]);
    const types = suggestCharts(cols(["region", "VARCHAR"], ["category", "VARCHAR"], ["units", "DOUBLE"]), rows).map((s) => s.config.type);
    expect(types).toContain("heatmap");
    expect(types).toContain("stacked_bar");
  });

  it("suggests a box plot for many values per category and a histogram for one measure", () => {
    const rows = Array.from({ length: 200 }, (_, i) => [["Lumber", "Roofing", "Tools"][i % 3], (i * 37) % 101]);
    const types = suggestCharts(cols(["category", "VARCHAR"], ["discount_pct", "DOUBLE"]), rows).map((s) => s.config.type);
    expect(types[0]).toBe("box");
    const hist = suggestChart(cols(["amount", "DOUBLE"]), Array.from({ length: 100 }, (_, i) => [i * 1.5]));
    expect(hist.type).toBe("histogram");
  });

  it("suggests a scatter for two measures", () => {
    const rows = Array.from({ length: 30 }, (_, i) => [i, i * 2 + 1]);
    expect(suggestChart(cols(["discount", "DOUBLE"], ["margin", "DOUBLE"]), rows).type).toBe("scatter");
  });

  it("never suggests a pie chart and always offers a table", () => {
    const s = suggestCharts(cols(["region", "VARCHAR"], ["revenue", "DOUBLE"]), [["A", 1], ["B", 2]]);
    expect(s.map((x) => x.config.type)).not.toContain("pie");
    expect(s.map((x) => x.config.type)).toContain("table");
    expect(compatibleTypes(cols(["region", "VARCHAR"]), [["A"]]).has("table")).toBe(true);
  });

  it("gives every suggestion a human-readable reason", () => {
    const s = suggestCharts(cols(["month", "DATE"], ["revenue", "DOUBLE"]), [["2026-01-01", 1], ["2026-02-01", 2]]);
    s.forEach((x) => expect(x.reason.length).toBeGreaterThan(10));
  });
});
