import { describe, expect, it } from "vitest";
import type { Finding } from "@/lib/api/types";
import { ApiError } from "@/lib/api/client";
import type { ReportBlockSpec } from "@/lib/api/resources/outputs";
import {
  publishRefusal,
  buildSummaryBlock,
  collectSources,
  dateRangeFor,
  editBlock,
  latestFullMonth,
  moveBlock,
  nudgeLayout,
  placeTile,
  presetFromRange,
  presetRange,
  reconcileLayout,
  reviewState,
} from "./model";

const today = new Date(Date.UTC(2026, 8, 18)); // 2026-09-18

describe("date presets", () => {
  it("resolves calendar presets relative to today", () => {
    expect(presetRange("last_month", today)).toEqual({ start: "2026-08-01", end: "2026-08-31" });
    expect(presetRange("this_month", today)).toEqual({ start: "2026-09-01", end: "2026-09-18" });
    expect(presetRange("qtd", today)).toEqual({ start: "2026-07-01", end: "2026-09-18" });
    expect(presetRange("ytd", today)).toEqual({ start: "2026-01-01", end: "2026-09-18" });
    expect(presetRange("rolling_30", today)).toEqual({ start: "2026-08-20", end: "2026-09-18" });
    expect(presetRange("all", today)).toBeNull();
  });

  it("honours a fiscal year start month", () => {
    // Fiscal year starting in February: Q3 of FY runs Aug–Oct.
    expect(presetRange("qtd", today, 2)).toEqual({ start: "2026-08-01", end: "2026-09-18" });
    expect(presetRange("ytd", today, 10)).toEqual({ start: "2025-10-01", end: "2026-09-18" });
  });

  it("handles year boundaries", () => {
    expect(presetRange("last_month", new Date(Date.UTC(2026, 0, 5)))).toEqual({ start: "2025-12-01", end: "2025-12-31" });
    expect(latestFullMonth(new Date(Date.UTC(2026, 0, 5)))).toBe("2025-12");
    expect(latestFullMonth(today)).toBe("2026-08");
  });

  it("round-trips request bodies for the server calendar", () => {
    expect(dateRangeFor("last_month")).toEqual({ text: "last month" });
    expect(dateRangeFor("all")).toBeNull();
    expect(dateRangeFor("custom", { start: "2026-01-01", end: "2026-03-31" })).toEqual({ start: "2026-01-01", end: "2026-03-31" });
    expect(dateRangeFor("custom", null)).toBeNull();
    expect(presetFromRange({ text: "YTD" }).preset).toBe("ytd");
    expect(presetFromRange({ start: "2026-01-01", end: "2026-02-01" })).toEqual({
      preset: "custom",
      custom: { start: "2026-01-01", end: "2026-02-01" },
    });
    expect(presetFromRange(null).preset).toBe("all");
  });
});

describe("grid layout", () => {
  it("places new tiles in the first free slot", () => {
    const layout = [{ i: "a", x: 0, y: 0, w: 6, h: 4 }];
    expect(placeTile(layout, "b", { w: 6, h: 4 })).toEqual({ i: "b", x: 6, y: 0, w: 6, h: 4 });
    expect(placeTile([...layout, { i: "b", x: 6, y: 0, w: 6, h: 4 }], "c", { w: 3, h: 3 })).toMatchObject({ x: 0, y: 4 });
  });

  it("nudges within bounds as a keyboard fallback for drag and resize", () => {
    const l = [{ i: "a", x: 0, y: 0, w: 12, h: 2 }];
    expect(nudgeLayout(l, "a", "left")[0].x).toBe(0);
    expect(nudgeLayout(l, "a", "right")[0].x).toBe(0);
    expect(nudgeLayout(l, "a", "wider")[0].w).toBe(12);
    expect(nudgeLayout(l, "a", "narrower", { w: 3, h: 2 })[0].w).toBe(11);
    expect(nudgeLayout(l, "a", "shorter", { w: 3, h: 2 })[0].h).toBe(2);
    expect(nudgeLayout(l, "a", "down")[0].y).toBe(1);
  });

  it("reconciles stored layout with tiles", () => {
    const out = reconcileLayout([{ i: "gone", x: 0, y: 0, w: 3, h: 3 }], [{ id: "k", kind: "kpi" }]);
    expect(out).toEqual([{ i: "k", x: 0, y: 0, w: 3, h: 3 }]);
  });
});

const block = (b: Partial<ReportBlockSpec>): ReportBlockSpec => ({ id: Math.random().toString(36), type: "narrative", ...b });

describe("review gating", () => {
  it("cannot publish while an included block is unreviewed", () => {
    const blocks = [block({ reviewed: true }), block({ reviewed: false })];
    const s = reviewState({ status: "in_review", blocks });
    expect(s.canPublish).toBe(false);
    expect(s.pending).toHaveLength(1);
    expect(s.blockers.join(" ")).toMatch(/1 included block not yet reviewed/);
  });

  it("allows publishing once every included block is reviewed; excluded blocks do not count", () => {
    const blocks = [block({ reviewed: true }), block({ excluded: true })];
    expect(reviewState({ status: "in_review", blocks }).canPublish).toBe(true);
  });

  it("requires submitting for review first and at least one included block", () => {
    expect(reviewState({ status: "draft", blocks: [block({ reviewed: true })] }).canPublish).toBe(false);
    expect(reviewState({ status: "draft", blocks: [block({})] }).canSubmit).toBe(true);
    expect(reviewState({ status: "in_review", blocks: [block({ excluded: true })] }).canPublish).toBe(false);
    expect(reviewState({ status: "published", blocks: [block({ reviewed: true })] })).toMatchObject({ readOnly: true, canPublish: false });
  });

  it("clears a block's review when its content is edited", () => {
    const b = block({ markdown: "a", reviewed: true });
    expect(editBlock(b, { markdown: "b" }).reviewed).toBe(false);
    expect(editBlock(b, { excluded: true }).reviewed).toBe(true);
  });

  it("reorders blocks", () => {
    const [a, b] = [block({ id: "a" }), block({ id: "b" })];
    expect(moveBlock([a, b], "a", 1).map((x) => x.id)).toEqual(["b", "a"]);
    expect(moveBlock([a, b], "a", -1).map((x) => x.id)).toEqual(["a", "b"]);
  });
});

const finding = (f: Partial<Finding>): Finding =>
  ({
    id: Math.random().toString(36).slice(2),
    statement: "",
    statement_type: "observation",
    evidence_strength: "strong",
    evidence_reasons: [],
    status: "confirmed",
    filter_context: [],
    artifact_ids: [],
    version_no: 1,
    created_at: "2026-09-01",
    ...f,
  }) as Finding;

describe("publish refusals from the API (R-15)", () => {
  it("lists the unreviewed block ids from 409 unreviewed_blocks", () => {
    const err = new ApiError(409, "2 blocks are not reviewed", null, "unreviewed_blocks", { block_ids: ["b1", "b3"] });
    expect(publishRefusal(err)).toEqual({ kind: "unreviewed", blockIds: ["b1", "b3"], message: "2 blocks are not reviewed" });
  });
  it("recognises publishing a draft", () => {
    expect(publishRefusal(new ApiError(409, "Submit first", null, "not_in_review")).kind).toBe("not_in_review");
  });
  it("passes other errors through", () => {
    expect(publishRefusal(new Error("boom"))).toEqual({ kind: "other", message: "boom" });
  });
});

describe("executive summary", () => {
  it("uses confirmed findings only and keeps statement types in separate sections", () => {
    const b = buildSummaryBlock([
      finding({ statement: "Revenue decreased 11.8%.", statement_type: "observation" }),
      finding({ statement: "Dallas accounted for 54% of the decline.", statement_type: "supported_explanation" }),
      finding({ statement: "May relate to reduced marketing.", statement_type: "hypothesis", evidence_strength: "hypothesis_only" }),
      finding({ statement: "Draft claim", status: "draft" }),
      finding({ statement: "Rejected claim", status: "rejected", statement_type: "supported_explanation" }),
    ]);
    expect(b.observations?.map((x) => (typeof x === "string" ? x : x.text))).toEqual(["Revenue decreased 11.8%."]);
    expect(b.supported_explanations?.map((x) => (typeof x === "string" ? x : x.text))).toEqual(["Dallas accounted for 54% of the decline."]);
    expect(b.hypotheses?.map((x) => (typeof x === "string" ? x : x.text))).toEqual(["May relate to reduced marketing."]);
  });

  it("collects versioned sources from blocks", () => {
    const sources = collectSources([
      block({ type: "finding", finding_id: "f1", snapshot: { statement: "S", statement_type: "observation", evidence_strength: "strong", investigation_id: "i1", metric_version_ids: { revenue: 3 } } }),
      block({ type: "chart", provenance: { datasets: ["orders"], metric: "gross_margin", metric_version: 2 } }),
      block({ type: "finding", finding_id: "f2", excluded: true, snapshot: { statement: "X", statement_type: "observation", evidence_strength: "weak", investigation_id: "i9" } }),
    ]);
    expect(sources).toEqual(
      expect.arrayContaining([
        { kind: "investigation", label: "Investigation", ref_id: "i1" },
        { kind: "metric", label: "revenue", ref_id: "revenue", version: 3 },
        { kind: "dataset", label: "orders", ref_id: null },
        { kind: "metric", label: "gross_margin", ref_id: "gross_margin", version: 2 },
      ]),
    );
    expect(sources.some((s) => s.ref_id === "i9")).toBe(false);
  });
});
