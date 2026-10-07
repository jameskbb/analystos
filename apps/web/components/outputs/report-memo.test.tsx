import * as React from "react";
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { ReportBlockSpec } from "@/lib/api/resources/outputs";
import { BlockView, bridgeFromBlock, readingBlocks } from "./report-blocks";

const steps = [
  { node_id: "a", label: "Unit-cost inflation", amount: -60, group_label: "Gross Margin", running_total: 140 },
  { node_id: "b", label: "Freight overrun", amount: -40, group_label: "Operating Expenses", running_total: 100 },
];

describe("operating review memo", () => {
  it("lays out headline, premise, bridge, causes, related signals and watch list", () => {
    const block: ReportBlockSpec = {
      id: "r",
      type: "narrative",
      label: "Operating review",
      title: "Operating review: Profit vs plan",
      headline: "Profit missed plan by $100.",
      premise: "The premise holds: revenue met its target.",
      causes: ["Unit-cost inflation, -$60: Lumber, mostly one supplier"],
      related: ["Gulf Coast lead conversion fell; relationship not established"],
      watch: ["Lumber unit cost vs plan"],
      reconciled: true,
      bridge: { metric_id: "profit", metric_label: "Profit", metric_format: "currency", start_label: "Plan", start: 200, end_label: "Actual", end: 100, steps },
      markdown: "should not render when structured",
    };
    render(<BlockView block={block} />);
    const memo = screen.getByTestId("block-operating-review");
    expect(within(memo).getByText("Reconciles exactly")).toBeInTheDocument();
    expect(memo.querySelector("[data-memo=headline]")).toHaveTextContent("Profit missed plan by $100.");
    expect(memo.querySelector("[data-memo=premise]")).toHaveTextContent("revenue met its target");
    expect(within(memo).getByRole("table", { name: "Profit bridge from plan to actual" })).toBeInTheDocument();
    expect(memo.querySelector("[data-memo=explains]")).toHaveTextContent("mostly one supplier");
    expect(memo.querySelector("[data-memo=related]")).toHaveTextContent("relationship not established");
    expect(memo.querySelector("[data-memo=watch]")).toHaveTextContent("Lumber unit cost vs plan");
    expect(memo).not.toHaveTextContent("should not render");
  });

  it("falls back to the markdown and says what is not yet confirmed", () => {
    render(<BlockView block={{ id: "r", type: "narrative", label: "Operating review", markdown: "Partial review.", pending: ["Operating Expenses"] }} />);
    const memo = screen.getByTestId("block-operating-review");
    expect(memo).toHaveTextContent("Not yet confirmed: 1 part awaiting review");
    expect(memo).toHaveTextContent("Partial review.");
  });
});

describe("bridge block", () => {
  it("renders the chart and table from the block's steps", () => {
    const block: ReportBlockSpec = { id: "b", type: "bridge", title: "Plan to actual", steps, start: 200, end: 100, gap: -100, metric_label: "Profit", format: "currency" };
    render(<BlockView block={block} />);
    const fig = screen.getByTestId("block-bridge");
    expect(within(fig).getByText("Plan to actual")).toBeInTheDocument();
    expect(within(fig).getByRole("table", { name: "Profit bridge from plan to actual" })).toHaveTextContent("Freight overrun");
    expect(bridgeFromBlock({ id: "x", type: "bridge" })).toBeNull();
  });
});

describe("reading view", () => {
  it("folds the bridge and its table into the memo and drops a heading that repeats the title", () => {
    const blocks: ReportBlockSpec[] = [
      { id: "h", type: "heading", text: "Operating review: Profit" },
      { id: "r", type: "narrative", label: "Operating review", title: "Operating review: Profit", markdown: "memo" },
      { id: "b", type: "bridge", steps, start: 200, end: 100, metric_label: "Profit" },
      { id: "t", type: "table", title: "Plan to actual" },
      { id: "s", type: "sources" },
    ];
    const read = readingBlocks(blocks, "Operating review: Profit");
    expect(read.map((b) => b.id)).toEqual(["r", "s"]);
    expect(read[0].title).toBeNull();
    expect(read[0].bridge?.steps).toHaveLength(2);
    // Anything else is left as it is.
    expect(readingBlocks([{ id: "n", type: "narrative", markdown: "x" }], "T").map((b) => b.id)).toEqual(["n"]);
  });
});
