import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DiffView, formatDiffValue, nodeFieldChanges } from "./diff-view";
import type { InvestigationDiff } from "@/lib/api/types";

const DIFF: InvestigationDiff = {
  node_changes: [
    {
      node_id: "dallas",
      change: "changed",
      statement_before: "Dallas -19.4%",
      statement_after: "Dallas -7.0%",
      fields: { pct_change: [-0.194, -0.07] },
    },
    { node_id: "new", change: "added", statement_after: "Houston -4.1%" },
  ],
  dataset_version_changes: [
    { table: "orders", before: { content_hash: "aaaaaaaa11", row_count: 1000 }, after: { content_hash: "bbbbbbbb22", row_count: 1200 } },
  ],
  metric_version_changes: [{ metric_id: "revenue", before: "3", after: "4" }],
};

describe("DiffView", () => {
  it("shows node before → after values and the data and definitions that changed", async () => {
    const onSelect = vi.fn();
    render(<DiffView diff={DIFF} onSelectNode={onSelect} />);
    expect(screen.getByText("Dallas -19.4%")).toHaveClass("line-through");
    expect(screen.getByText("Dallas -7.0%")).toBeInTheDocument();
    expect(screen.getByText("−19.4%")).toBeInTheDocument();
    expect(screen.getByText("−7.0%")).toBeInTheDocument();
    expect(screen.getByText("orders")).toBeInTheDocument();
    expect(screen.getByText(/1,200 rows/)).toBeInTheDocument();
    expect(screen.getByText("Revenue")).toBeInTheDocument();
    const row = screen.getByText("Dallas -7.0%").closest("li")!;
    await userEvent.setup().click(within(row).getByRole("button", { name: "Show" }));
    expect(onSelect).toHaveBeenCalledWith("dallas");
  });

  it("says so when a rerun reproduced everything", () => {
    render(<DiffView diff={{ node_changes: [], dataset_version_changes: [], metric_version_changes: [], unchanged_nodes: 9 }} />);
    expect(screen.getByText(/reproduced every node exactly \(9 nodes\)/)).toBeInTheDocument();
  });

  it("normalizes field changes from snapshots and formats shares as percentages", () => {
    expect(
      nodeFieldChanges({ change: "changed", fields: ["current"], before: { current: 10 }, after: { current: 12 } }),
    ).toEqual([{ field: "current", before: 10, after: 12 }]);
    expect(formatDiffValue("contribution_to_parent", { share: 0.54 })).toBe("+54.0%");
  });
});
