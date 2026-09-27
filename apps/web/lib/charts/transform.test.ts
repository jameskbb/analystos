import { describe, expect, it } from "vitest";
import { boxStats, histogram, quantile, toSeries, waterfallSteps } from "./transform";

describe("chart transforms", () => {
  it("pivots long rows into aligned series", () => {
    const cols = [
      { name: "m", type: "DATE" },
      { name: "region", type: "VARCHAR" },
      { name: "rev", type: "DOUBLE" },
    ];
    const { categories, series } = toSeries(
      cols,
      [
        ["2026-08-01", "Dallas", 5],
        ["2026-07-01", "Dallas", 7],
        ["2026-07-01", "Austin", 2],
      ],
      "m",
      ["rev"],
      "region",
    );
    expect(categories).toEqual(["2026-07-01", "2026-08-01"]);
    expect(series.find((s) => s.name === "Dallas")!.data).toEqual([7, 5]);
    expect(series.find((s) => s.name === "Austin")!.data).toEqual([2, null]);
  });

  it("computes numpy-style quantiles and Tukey box stats", () => {
    expect(quantile([1, 2, 3, 4], 0.5)).toBe(2.5);
    const [b] = boxStats([["a", 1], ["a", 2], ["a", 3], ["a", 4], ["a", 100]], 0, 1);
    expect(b.median).toBe(3);
    expect(b.outliers).toEqual([100]);
    expect(b.max).toBe(4);
  });

  it("bins a histogram that accounts for every value", () => {
    const vals = Array.from({ length: 500 }, (_, i) => (i * 7919) % 1000);
    const h = histogram(vals);
    expect(h.counts.reduce((a, b) => a + b, 0)).toBe(500);
    expect(h.edges.length).toBe(h.counts.length + 1);
  });

  it("builds a waterfall bridge from start through deltas to end", () => {
    const steps = waterfallSteps(["July revenue", "Dallas", "Roofing", "August revenue"], [100, -20, 5, 85]);
    expect(steps.map((s) => s.kind)).toEqual(["delta", "delta", "delta", "delta", "end"]);
    const anchored = waterfallSteps(["Start", "Dallas", "Roofing", "End"], [100, -20, 5, 85]);
    expect(anchored.map((s) => s.kind)).toEqual(["start", "delta", "delta", "end"]);
    expect(anchored[1]).toMatchObject({ base: 80, value: -20 });
    expect(anchored[2]).toMatchObject({ base: 80, value: 5 });
  });
});
