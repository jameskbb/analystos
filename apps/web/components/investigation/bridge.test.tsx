import * as React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Bridge, TreeNode } from "@/lib/api/types";
import { normalizeTree } from "./model";
import { bridgeBars, bridgeFromReconciliation, bridgeGap, bridgeGroups, bridgeOf } from "./bridge";
import { BridgeChart, BridgeTable, wrapLabel } from "./bridge-chart";
import { isOtherLines, storyExpansion } from "./answer";
import { PLAN_TREE_FIXTURE } from "./fixtures.test-data";

const BRIDGE: Bridge = {
  metric_id: "profit",
  metric_label: "Profit",
  metric_format: "currency",
  start_label: "Plan",
  start: 200,
  end_label: "Actual",
  end: 100,
  gap: -100,
  steps: [
    { node_id: "cost", label: "Unit-cost inflation", amount: -60, group_label: "Gross Margin", group_node_id: "gm", running_total: 140 },
    { node_id: "mix", label: "Favorable volume and mix", amount: 10, group_label: "Gross Margin", group_node_id: "gm", running_total: 150 },
    { node_id: "freight", label: "Freight overrun", amount: -45, group_label: "Operating Expenses", group_node_id: "opex", running_total: 105 },
    { node_id: "other", label: "Other lines, net", amount: -5, group_label: "Operating Expenses", group_node_id: "opex", running_total: 100 },
  ],
  reconciled: true,
};

describe("bridge layout", () => {
  it("floats each step from the running total before it to the one after", () => {
    const bars = bridgeBars(BRIDGE);
    expect(bars.map((b) => [b.kind, b.label, b.from, b.to])).toEqual([
      ["total", "Plan", 0, 200],
      ["step", "Unit-cost inflation", 200, 140],
      ["step", "Favorable volume and mix", 140, 150],
      ["step", "Freight overrun", 150, 105],
      ["step", "Other lines, net", 105, 100],
      ["total", "Actual", 0, 100],
    ]);
    expect(bridgeGap(BRIDGE)).toBe(0);
    expect(bridgeGroups(BRIDGE)).toEqual([
      { group: "Gross Margin", group_node_id: "gm", first: 0, last: 1 },
      { group: "Operating Expenses", group_node_id: "opex", first: 2, last: 3 },
    ]);
  });

  it("reports a bridge whose steps do not reach the actual instead of hiding it", () => {
    expect(bridgeGap({ ...BRIDGE, end: 90 })).toBe(-10);
  });

  it("reads a bridge off the reconciliation when the engine publishes none, leaves first", () => {
    const tree = normalizeTree(PLAN_TREE_FIXTURE);
    const b = bridgeOf({ bridge: null }, tree)!;
    expect(b.start).toBe(200);
    expect(b.end).toBe(100);
    expect(b.steps.map((s) => [s.label, s.amount, s.group_label])).toEqual([
      ["Gross Margin", -75, "Gross Margin"],
      ["Operating Expenses", -25, "Operating Expenses"],
    ]);
    expect(bridgeOf({ bridge: BRIDGE }, tree)).toBe(BRIDGE);
    const recon = tree.nodes.get("recon")!.reconciliation!;
    expect(bridgeFromReconciliation({ ...recon, reconciled: false })).toBeNull();
    const nested = bridgeFromReconciliation({
      ...recon,
      identities: [
        ...recon.identities,
        { parent_node_id: "gm", parent_label: "Gross Margin", parent_amount: -75, components: [{ node_id: "c", label: "Unit cost", amount: -80 }, { node_id: "m", label: "Mix", amount: 5 }], sum_of_components: -75, residual: 0, reconciled: true },
      ],
    })!;
    expect(nested.steps.map((s) => s.label)).toEqual(["Unit cost", "Mix", "Operating Expenses"]);
  });

  it("wraps long labels onto two lines", () => {
    expect(wrapLabel("Favorable volume and mix")).toEqual(["Favorable volume", "and mix"]);
    expect(wrapLabel("Plan")).toEqual(["Plan"]);
  });
});

describe("BridgeChart and BridgeTable", () => {
  it("draws plan, signed steps and actual, and selects a step's node", async () => {
    const onSelect = vi.fn();
    render(<BridgeChart bridge={BRIDGE} onSelect={onSelect} />);
    const chart = screen.getByRole("img", { name: /Profit: Plan \$200, Unit-cost inflation −\$60/ });
    expect(chart).toBeInTheDocument();
    expect(chart.querySelectorAll("[data-bar=step]")).toHaveLength(4);
    await userEvent.click(screen.getByRole("button", { name: /Freight overrun −\$45: open the node/ }));
    expect(onSelect).toHaveBeenCalledWith("freight");
    expect(screen.getByTestId("bridge-chart")).toHaveTextContent("Operating Expenses");
  });

  it("lists the same steps in a table from plan to actual", () => {
    render(<BridgeTable bridge={BRIDGE} />);
    const table = screen.getByRole("table", { name: "Profit bridge from plan to actual" });
    const rows = within(table).getAllByRole("row");
    expect(rows[0]).toHaveTextContent("Profit, plan$200");
    expect(rows[3]).toHaveTextContent("Freight overrun · Operating Expenses−$45");
    expect(rows[rows.length - 1]).toHaveTextContent("Profit, actual$100");
  });
});

describe("the story view", () => {
  const node = (n: Partial<TreeNode> & Pick<TreeNode, "id" | "statement">): TreeNode => ({
    parent_id: "root",
    statement_type: "supported_explanation",
    evidence_strength: "strong",
    evidence_reasons: [],
    artifact_ids: [],
    status: "proposed",
    children: [],
    ...n,
  });
  const tree = normalizeTree({
    root_id: "root",
    nodes: [
      node({ id: "root", parent_id: null, statement: "Profit missed plan", root_effect: -100, abs_change: -100, children: ["gm", "opex", "byregion", "premise"] }),
      node({ id: "gm", statement: "GM", root_effect: -70, children: ["cost"] }),
      node({ id: "cost", parent_id: "gm", statement: "Unit cost", root_effect: -60, children: ["bycat"] }),
      node({ id: "bycat", parent_id: "cost", kind: "dimension", statement: "by category", children: ["lumber", "tools"] }),
      node({ id: "lumber", parent_id: "bycat", kind: "segment", statement: "Lumber", root_effect: -55, children: ["bysup"] }),
      node({ id: "tools", parent_id: "bycat", kind: "segment", statement: "Tools", root_effect: -1, children: ["x"] }),
      node({ id: "x", parent_id: "tools", statement: "x", root_effect: -1 }),
      node({ id: "bysup", parent_id: "lumber", kind: "dimension", statement: "by supplier", children: ["gulf"] }),
      node({ id: "gulf", parent_id: "bysup", kind: "segment", statement: "Gulf States", root_effect: -50 }),
      node({ id: "opex", statement: "Opex", root_effect: -30, children: ["labor", "other"] }),
      node({ id: "labor", parent_id: "opex", statement: "Labor", root_effect: -4, children: ["bybranch"] }),
      node({ id: "bybranch", parent_id: "labor", kind: "dimension", statement: "by branch", children: ["sa"] }),
      node({ id: "sa", parent_id: "bybranch", kind: "segment", statement: "San Antonio", root_effect: -3 }),
      node({ id: "other", parent_id: "opex", kind: "other", title: "Other lines, net", statement: "Three lines", root_effect: -1, members: [{ label: "Occupancy", amount: 0 }], children: [] }),
      node({ id: "byregion", kind: "dimension", statement: "gap by region", children: ["gc"] }),
      node({ id: "gc", parent_id: "byregion", kind: "segment", statement: "Gulf Coast", root_effect: -40 }),
      node({ id: "premise", kind: "check", role: "premise", statement: "premise", children: [] }),
      node({ id: "recon", kind: "check", role: "reconciliation", statement: "recon", reconciliation: { metric_id: "profit", metric_label: "Profit", actual: 1, baseline: 2, gap: -1, tolerance: 0, reconciled: true, identities: [] } }),
    ],
  });

  it("opens the path to the operational causes and keeps small lines, other lines and overlapping views closed", () => {
    const open = storyExpansion(tree)!;
    expect([...open].sort()).toEqual(["bycat", "bysup", "cost", "gm", "lumber", "opex", "root"].sort());
    expect(open.has("tools")).toBe(false); // immaterial segment
    expect(open.has("labor")).toBe(false); // below 5% of the gap
    expect(open.has("byregion")).toBe(false); // overlapping view
  });

  it("returns null for a tree without root effects", () => {
    expect(storyExpansion(normalizeTree({ root_id: "r", nodes: [node({ id: "r", parent_id: null, statement: "r" })] }))).toBeNull();
  });

  it("recognises the other-lines node", () => {
    expect(isOtherLines(tree.nodes.get("other")!)).toBe(true);
    expect(isOtherLines(tree.nodes.get("gm")!)).toBe(false);
  });
});
