import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ArtifactRecord } from "@/lib/api/types";
import { CalculationView, readCalculation } from "./calculation";

const contribution: ArtifactRecord = {
  id: "a1",
  engine_id: "art_1",
  kind: "metric_result",
  title: "Revenue change by Branch",
  sql: "",
  created_at: "2026-09-18T00:00:00Z",
  parent_ids: ["art_q1", "art_q2"],
  data: {
    method: "additive",
    additive_valid: true,
    total_current: 10024461.68,
    total_baseline: 11365541.11,
    total_change: -1341079.43,
    segment_count: 2,
    notes: ["Segments partition the total."],
    rows: [
      { segment: "Dallas", current: 1000, baseline: 1662000, effect: -662000, share_of_change: 0.494, mix_effect: -400000, rate_effect: -262000, pct_change: -0.3 },
      { segment: "Houston", current: 900, baseline: 950, effect: -50, share_of_change: 0.0001, mix_effect: null, rate_effect: null, pct_change: -0.05 },
    ],
  },
};

describe("readCalculation", () => {
  it("reads contribution data with totals and per-segment effects", () => {
    const c = readCalculation(contribution);
    expect(c?.kind).toBe("contribution");
    if (c?.kind !== "contribution") return;
    expect(c.additiveValid).toBe(true);
    expect(c.totalChange).toBeCloseTo(-1341079.43);
    expect(c.rows[0]).toMatchObject({ segment: "Dallas", effect: -662000, share: 0.494, mix: -400000, rate: -262000 });
  });
  it("reads decomposition data", () => {
    const c = readCalculation({
      data: {
        method: "lmdi",
        parent_current: 10,
        parent_baseline: 12,
        parent_change: -2,
        residual: 0,
        residual_share: 0,
        effects: [{ metric_id: "orders", relation: "multiplicative", current: 5, baseline: 6, effect: -1.1, share: 0.55 }],
        notes: [],
      },
    });
    expect(c?.kind).toBe("decomposition");
  });
  it("returns null without data and raw for unknown shapes", () => {
    expect(readCalculation({ data: undefined })).toBeNull();
    expect(readCalculation({ data: { anything: 1 } })?.kind).toBe("raw");
  });
});

describe("CalculationView", () => {
  it("shows the method, the additivity check, totals and each segment's effect and share", () => {
    render(<CalculationView artifact={contribution} format="currency" />);
    expect(screen.getByText(/Shares add up exactly/)).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Segment effects" });
    const dallas = within(table).getAllByRole("row")[1];
    expect(dallas).toHaveTextContent("Dallas");
    expect(dallas).toHaveTextContent("49%");
    expect(dallas).toHaveTextContent("-$662K");
  });
  it("hides shares when the segments are not additive", () => {
    const na = { ...contribution, data: { ...contribution.data, method: "non_additive", additive_valid: false } };
    render(<CalculationView artifact={na} format="currency" />);
    expect(screen.getByText(/Not additive: shares not valid/)).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "Segment effects" })).getAllByTitle("Not defined for overlapping segments")).toHaveLength(2);
  });
  it("links the parent query artifacts and opens them in place", async () => {
    const opened: string[] = [];
    const parent: ArtifactRecord = { id: "q1", engine_id: "art_q1", kind: "query", title: "Revenue by branch, Aug", sql: "select 1", created_at: "" };
    render(<CalculationView artifact={contribution} artifacts={[parent]} onOpenArtifact={(id) => opened.push(id)} />);
    expect(screen.getByText(/Computed from 2 queries/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Revenue by branch, Aug" }));
    expect(opened).toEqual(["q1"]);
  });
});
