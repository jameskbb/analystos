import * as React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { TreeNode } from "@/lib/api/types";
import { normalizeTree, premiseVerdict } from "./model";
import { explanationTree, higherIsBetterFor, labelWithoutUnit, overlappingViewIds, signalRecord, gapPhrase, premiseNodes, reconciliationNode, relatedSignalNodes, relationshipLabel, valueLabels } from "./answer";
import { AnswerStrip, reconciliationSentence } from "./answer-strip";
import { RelatedSignalsList } from "./related-signals";
import { InvestigationTreeView } from "./investigation-tree";
import { ReconciliationView } from "./reconciliation-view";
import { PLAN_TREE_FIXTURE } from "./fixtures.test-data";

const tree = normalizeTree(PLAN_TREE_FIXTURE);

describe("answer helpers", () => {
  it("finds the premise, the reconciliation and the top-level related signals", () => {
    expect(premiseNodes(tree).map((n) => n.id)).toEqual(["premise"]);
    expect(premiseVerdict(tree.nodes.get("premise")!)).toBe("holds");
    expect(reconciliationNode(tree)?.id).toBe("recon");
    expect(relatedSignalNodes(tree).map((n) => n.id)).toEqual(["signal"]);
  });

  it("keeps related signals out of the explanation tree but selectable", () => {
    const shown = explanationTree(tree);
    // Reconciled parts first, then the premise and the reconciliation check.
    expect(shown.nodes.get("root")!.children).toEqual(["gm", "opex", "premise", "recon"]);
    expect(shown.nodes.has("signal")).toBe(true);
    expect(tree.nodes.get("root")!.children).toContain("signal");
  });

  it("labels plan comparisons Actual / Plan / Variance and words the gap", () => {
    expect(valueLabels(tree.nodes.get("root")!)).toEqual({ current: "Actual", baseline: "Plan", change: "Variance" });
    expect(valueLabels({ baseline_label: "Q1 2026" })).toEqual({ current: "Current", baseline: "Q1 2026", change: "Change" });
    expect(valueLabels({ baseline_label: null })).toEqual({ current: "Current", baseline: "Baseline", change: "Change" });
    expect(gapPhrase(tree.nodes.get("root"))).toBe("Profit miss");
    expect(relationshipLabel(tree.nodes.get("root"))).toBe("Relationship to the Profit miss: not established");
    expect(gapPhrase({ metric_label: "Revenue", baseline_label: "July 2026", abs_change: -1 })).toBe("Revenue change");
  });

  it("colours a change by its effect on the root", () => {
    expect(higherIsBetterFor(tree.nodes.get("opex")!)).toBe(false);
    expect(higherIsBetterFor(tree.nodes.get("gm")!)).toBe(true);
    expect(higherIsBetterFor({ abs_change: 5, pct_change: 0.1, root_effect: null })).toBe(true);
    expect(higherIsBetterFor({ abs_change: null, pct_change: 0.037, root_effect: -942 })).toBe(false);
  });

  it("writes the reconciliation as parts = gap from the engine's numbers", () => {
    expect(reconciliationSentence(tree.nodes.get("recon")!.reconciliation!)).toBe("Gross Margin −$75 + Operating Expenses −$25 = −$100");
  });
});

describe("signals, units and overlapping views", () => {
  it("finds a signal's record, strips a repeated unit and flags dimension breakdowns next to a reconciliation", () => {
    expect(signalRecord([{ node_id: "signal", provisional: true } as never], "signal")?.provisional).toBe(true);
    expect(signalRecord(undefined, "signal")).toBeNull();
    expect(labelWithoutUnit("Median First Response (hours)", "hours")).toBe("Median First Response");
    expect(labelWithoutUnit("Median First Response", "hours")).toBe("Median First Response");
    expect(labelWithoutUnit("Revenue", null)).toBe("Revenue");
    const withGroup = normalizeTree({
      ...PLAN_TREE_FIXTURE,
      nodes: [
        ...(PLAN_TREE_FIXTURE.nodes as TreeNode[]).map((n) => (n.id === "root" ? { ...n, children: [...(n.children as string[]), "byregion"] } : n)),
        { id: "byregion", parent_id: "root", kind: "dimension", statement: "Profit gap to plan by Region", statement_type: "observation", evidence_strength: "strong", evidence_reasons: [], artifact_ids: [], status: "proposed", children: [] },
      ],
    });
    expect([...overlappingViewIds(withGroup)]).toEqual(["byregion"]);
    expect(overlappingViewIds(normalizeTree({ root_id: "r", nodes: [{ id: "r", parent_id: null, statement: "x", statement_type: "observation", evidence_strength: "strong", evidence_reasons: [], artifact_ids: [], status: "proposed", children: [] }] })).size).toBe(0);
  });
});

describe("AnswerStrip", () => {
  it("shows the premise verdict, the headline gap and the reconciliation, each selecting its node", async () => {
    const onSelect = vi.fn();
    render(<AnswerStrip tree={tree} interpretation={{ metric_ids: [], ambiguous: [], filters: [], intent: "why_change", confidence_notes: [], premises: [{ text: "we made our sales number", term: "revenue", metric_id: "revenue", expectation: "meets_plan" }] }} onSelect={onSelect} />);
    const premise = screen.getByTestId("premise-card");
    expect(premise).toHaveAccessibleName("Premise “we made our sales number”: supported");
    expect(premise).toHaveTextContent("Supported by the data");
    expect(premise).toHaveTextContent("Actual $1,003");
    expect(premise).toHaveTextContent("Plan $1,000");
    expect(screen.getByTestId("headline-card")).toHaveTextContent("−$100");
    expect(screen.getByTestId("reconciliation-card")).toHaveTextContent("Adds back to the gap");
    await userEvent.click(screen.getByTestId("reconciliation-card"));
    expect(onSelect).toHaveBeenCalledWith("recon");
  });

  it("renders nothing for an investigation without premises or a reconciliation", () => {
    const { container } = render(<AnswerStrip tree={normalizeTree({ root_id: "r", nodes: [{ id: "r", parent_id: null, statement: "x", statement_type: "observation", evidence_strength: "strong", evidence_reasons: [], artifact_ids: [], status: "proposed", children: [] }] })} onSelect={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("related signals and the tree", () => {
  it("lists related signals apart, each saying the relationship is not established", async () => {
    const onSelect = vi.fn();
    render(<RelatedSignalsList tree={tree} signals={[{ node_id: "signal", provisional: true } as never]} selectedId={null} onSelect={onSelect} />);
    const section = screen.getByTestId("related-signals");
    expect(within(section).getByText("Related signals")).toBeInTheDocument();
    const row = within(section).getByRole("button", { name: /Related signal: Lead conversion/ });
    expect(row).toHaveTextContent("Relationship to the Profit miss: not established");
    expect(row).toHaveTextContent("provisional");
    // The supporting measurement in the same segment is listed under its signal.
    expect(within(section).getByRole("button", { name: /Supporting measurement: Median first response/i })).toBeInTheDocument();
    await userEvent.click(row);
    expect(onSelect).toHaveBeenCalledWith("signal");
  });

  it("shows executive titles, effects on the root and the reconciliation verdict in the tree", () => {
    const shown = explanationTree(tree);
    render(<InvestigationTreeView tree={shown} selectedId="root" onSelect={() => {}} expanded={new Set(["root"])} onExpandedChange={() => {}} />);
    const items = screen.getAllByRole("treeitem");
    expect(items.map((i) => i.getAttribute("data-node-id"))).toEqual(["root", "gm", "opex", "premise", "recon"]);
    expect(items[1]).toHaveTextContent("Gross margin below plan");
    expect(items[1]).toHaveTextContent("−$75 on Profit");
    expect(items[3]).toHaveTextContent("premise holds");
    expect(items[4]).toHaveTextContent("reconciles");
  });

  it("renders each reconciliation identity with its residual", async () => {
    const onSelect = vi.fn();
    render(<ReconciliationView reconciliation={tree.nodes.get("recon")!.reconciliation!} onSelect={onSelect} />);
    const table = screen.getByRole("table", { name: "Reconciliation identities" });
    expect(table).toHaveTextContent("Profit gap");
    expect(screen.getByText("Reconciles")).toBeInTheDocument();
    await userEvent.click(within(table).getByRole("button", { name: "Gross Margin" }));
    expect(onSelect).toHaveBeenCalledWith("gm");
  });
});
