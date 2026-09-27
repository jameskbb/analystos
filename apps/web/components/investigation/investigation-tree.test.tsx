import * as React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InvestigationTreeView } from "./investigation-tree";
import { defaultExpanded, normalizeTree, parentFormatOf, periodCandidateLabel, premiseVerdict, visibleNodes } from "./model";
import { TREE_FIXTURE } from "./fixtures.test-data";

function Harness({ onSelect }: { onSelect?: (id: string) => void }) {
  const tree = React.useMemo(() => normalizeTree(TREE_FIXTURE), []);
  const [expanded, setExpanded] = React.useState(() => new Set(["root"]));
  const [selected, setSelected] = React.useState<string | null>("root");
  return (
    <InvestigationTreeView
      tree={tree}
      selectedId={selected}
      onSelect={(id) => {
        setSelected(id);
        onSelect?.(id);
      }}
      expanded={expanded}
      onExpandedChange={setExpanded}
    />
  );
}

describe("normalizeTree", () => {
  it("normalizes contribution objects and numbers into shares", () => {
    const t = normalizeTree(TREE_FIXTURE);
    expect(t.rootId).toBe("root");
    expect(t.nodes.get("units")!.share).toBe(0.67);
    expect(t.nodes.get("austin")!.share).toBe(-0.05);
    expect(t.nodes.get("root")!.format).toBe("currency");
  });

  it("marks nodes saved as findings from Investigation.node_findings", () => {
    const t = normalizeTree(TREE_FIXTURE, { units: "f1" });
    expect(t.nodes.get("units")!.finding_id).toBe("f1");
    expect(t.nodes.get("root")!.finding_id).toBeNull();
  });

  it("accepts nested children and node maps", () => {
    const nested = normalizeTree({
      root_id: "r",
      nodes: {
        r: {
          id: "r",
          parent_id: null,
          statement: "root",
          statement_type: "observation",
          evidence_strength: "strong",
          evidence_reasons: [],
          artifact_ids: [],
          status: "proposed",
          children: [
            { id: "c", parent_id: null, statement: "child", statement_type: "observation", evidence_strength: "weak", evidence_reasons: [], artifact_ids: [], status: "proposed", children: [] },
          ],
        },
      },
    });
    expect(nested.nodes.get("c")!.parent_id).toBe("r");
    expect(visibleNodes(nested, new Set(["r"])).map((n) => n.id)).toEqual(["r", "c"]);
  });

  it("expands to a default depth", () => {
    const t = normalizeTree(TREE_FIXTURE);
    expect([...defaultExpanded(t, 2)].sort()).toEqual(["root", "units"]);
  });
});

describe("InvestigationTreeView", () => {
  it("renders nested nodes with statement types and contribution shares", () => {
    render(<Harness />);
    const tree = screen.getByRole("tree", { name: "Investigation tree" });
    const items = within(tree).getAllByRole("treeitem");
    expect(items.map((i) => i.getAttribute("data-node-id"))).toEqual(["root", "units", "asp"]);
    expect(items[0]).toHaveAttribute("aria-level", "1");
    expect(items[1]).toHaveAttribute("aria-level", "2");
    expect(items[1]).toHaveAccessibleName(/^Supported explanation: Unit volume declined 8.2%/);
    expect(items[2]).toHaveAccessibleName(/^Hypothesis:/);
    expect(within(items[2]).getByText(/Promotional discounting/)).toHaveClass("italic");
    expect(within(items[1]).getByText("67%")).toBeInTheDocument();
    expect(within(items[0]).getByText(/−11.8%/)).toBeInTheDocument();
  });

  it("navigates, expands and collapses with the keyboard", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} />);
    const root = screen.getAllByRole("treeitem")[0];
    root.focus();
    await user.keyboard("{ArrowDown}");
    expect(onSelect).toHaveBeenLastCalledWith("units");
    expect(screen.getAllByRole("treeitem")[1]).toHaveAttribute("aria-expanded", "false");
    await user.keyboard("{ArrowRight}");
    expect(screen.getAllByRole("treeitem").map((i) => i.getAttribute("data-node-id"))).toEqual([
      "root",
      "units",
      "dallas",
      "austin",
      "asp",
    ]);
    await user.keyboard("{ArrowRight}");
    expect(onSelect).toHaveBeenLastCalledWith("dallas");
    await user.keyboard("{ArrowLeft}");
    expect(onSelect).toHaveBeenLastCalledWith("units");
    await user.keyboard("{ArrowLeft}");
    expect(screen.getAllByRole("treeitem")).toHaveLength(3);
    await user.keyboard("{End}");
    expect(onSelect).toHaveBeenLastCalledWith("asp");
  });

  it("marks the selected node and styles rejected nodes as struck through", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByText("Unit volume declined 8.2%"));
    const units = screen.getAllByRole("treeitem")[1];
    expect(units).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowRight}");
    const austin = screen.getByRole("treeitem", { name: /Austin/ });
    expect(austin).toHaveAttribute("data-status", "rejected");
    expect(austin).toHaveAccessibleName(/\(rejected\)$/);
    expect(within(austin).getByText("Austin +2.8%")).toHaveClass("line-through");
  });
});

describe("premise checks (flow 2)", () => {
  it("reads the verdict from a check node's notes", () => {
    expect(premiseVerdict({ kind: "check", notes: ["premise holds"] })).toBe("holds");
    expect(premiseVerdict({ kind: "check", notes: ["premise contradicted"] })).toBe("contradicted");
    expect(premiseVerdict({ kind: "check", notes: [] })).toBeNull();
    expect(premiseVerdict({ kind: "driver", notes: ["premise holds"] })).toBeNull();
  });
  it("labels period candidates with the measured premise change", () => {
    const label = periodCandidateLabel({
      window: { start: "2026-04-01", end: "2026-07-01" },
      baseline: { start: "2025-04-01", end: "2025-07-01" },
      comparison_kind: "yoy",
      premise_changes: { revenue: 0.012 },
      holds: true,
    });
    expect(label).toContain("Revenue +1.2%");
    expect(label).not.toContain("does not hold");
  });
});

describe("parentFormatOf", () => {
  it("returns the nearest ancestor metric format (effects are in the parent's units)", () => {
    const t = normalizeTree({
      root_id: "r",
      nodes: [
        { id: "r", parent_id: null, statement: "GM%", statement_type: "observation", evidence_strength: "strong", evidence_reasons: [], artifact_ids: [], status: "proposed", children: ["d"], metric_format: "percent" },
        { id: "d", parent_id: "r", statement: "by category", statement_type: "observation", evidence_strength: "strong", evidence_reasons: [], artifact_ids: [], status: "proposed", children: ["c"] },
        { id: "c", parent_id: "d", statement: "COGS", statement_type: "supported_explanation", evidence_strength: "strong", evidence_reasons: [], artifact_ids: [], status: "proposed", children: [], metric_format: "currency" },
      ],
    });
    expect(parentFormatOf(t, t.nodes.get("c")!)).toBe("percent");
    expect(parentFormatOf(t, t.nodes.get("r")!)).toBeNull();
  });
});
