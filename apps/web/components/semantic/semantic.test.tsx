import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { DriverEdge } from "@/lib/api/types";
import type { SemanticModelSpec } from "@/lib/api/resources/semantic";
import { TooltipProvider } from "@/components/ui/tooltip";
import { MetricTreeEditor } from "./metric-tree-editor";
import {
  buildTreeRows,
  emptyMetricForm,
  findSynonymConflicts,
  formulaRefs,
  formulaSummary,
  mergeSuggestions,
  metricBaseEntities,
  metricPayloadFromForm,
  orphanEdges,
  reachableEntities,
  slugifyMetricId,
  termCandidates,
  validateMetricForm,
} from "./metric-utils";

describe("metric form logic", () => {
  it("requires entity and expression for simple metrics, but not an expression for count", () => {
    const f = { ...emptyMetricForm(), id: "revenue", label: "Revenue" };
    expect(validateMetricForm(f)).toMatchObject({ entity: expect.any(String), expr: expect.any(String) });
    expect(validateMetricForm({ ...f, entity: "order_line", agg: "count" })).toEqual({});
  });

  it("validates ratio inputs", () => {
    const f = { ...emptyMetricForm(), id: "aov", label: "AOV", kind: "ratio" as const, numerator: "revenue", denominator: "revenue" };
    expect(validateMetricForm(f).denominator).toMatch(/differ/);
    expect(validateMetricForm({ ...f, denominator: "orders" })).toEqual({});
  });

  it("rejects malformed ids", () => {
    expect(validateMetricForm({ ...emptyMetricForm(), id: "Gross Margin", label: "x", kind: "derived", formula: "a" }).id).toBeTruthy();
  });

  it("sends only the fields relevant to the metric kind", () => {
    const base = { ...emptyMetricForm(), id: "aov", label: "AOV", entity: "order_line", expr: "net_amount", numerator: "revenue", denominator: "orders", formula: "x" };
    const ratio = metricPayloadFromForm({ ...base, kind: "ratio", tags: "sales, kpi ,", synonyms: "average order value" });
    expect(ratio).toMatchObject({ kind: "ratio", numerator: "revenue", denominator: "orders", entity: null, expr: null, agg: null, formula: null });
    expect(ratio.tags).toEqual(["sales", "kpi"]);
    expect(ratio.synonyms).toEqual(["average order value"]);
    const simple = metricPayloadFromForm({ ...base, kind: "simple" });
    expect(simple).toMatchObject({ entity: "order_line", agg: "sum", expr: "net_amount", numerator: null, formula: null });
    expect(metricPayloadFromForm({ ...base, kind: "derived" })).toMatchObject({ formula: "x", entity: null, numerator: null });
  });

  it("sends time aggregation only for simple metrics and validates avg (semi-additive balances)", () => {
    const base = { ...emptyMetricForm(), id: "inventory_value", label: "Inventory", entity: "inventory", expr: "on_hand * unit_cost" };
    expect(metricPayloadFromForm({ ...base, time_aggregation: "last" }).time_aggregation).toBe("last");
    expect(metricPayloadFromForm({ ...base, kind: "ratio", numerator: "a", denominator: "b", time_aggregation: "last" }).time_aggregation).toBe("sum");
    expect(validateMetricForm({ ...base, agg: "max", time_aggregation: "avg" }).time_aggregation).toMatch(/sum or count/);
    expect(validateMetricForm({ ...base, agg: "sum", time_aggregation: "avg" }).time_aggregation).toBeUndefined();
    expect(formulaSummary({ kind: "simple", agg: "sum", expr: "value", entity: "inventory", time_aggregation: "last" })).toBe(
      "SUM(value) per inventory, last value in each period",
    );
  });

  it("slugifies labels into metric ids", () => {
    expect(slugifyMetricId("Gross Margin %")).toBe("gross_margin_pct");
    expect(slugifyMetricId("30-day Revenue")).toBe("m_30_day_revenue");
  });

  it("summarizes formulas per kind", () => {
    expect(formulaSummary({ kind: "simple", agg: "sum", expr: "net_amount", entity: "order_line" })).toBe("SUM(net_amount) per order_line");
    expect(formulaSummary({ kind: "simple", agg: "count_distinct", expr: "order_id" })).toBe("COUNT(DISTINCT order_id)");
    expect(formulaSummary({ kind: "ratio", numerator: "revenue", denominator: "orders" })).toBe("revenue ÷ orders");
    expect(formulaRefs("revenue - cogs * 2", ["revenue", "cogs", "orders"])).toEqual(["revenue", "cogs"]);
  });
});

describe("ambiguity detection", () => {
  const metrics = [
    { id: "gross_margin", name: "gross_margin", label: "Gross Margin", synonyms: ["margin"] },
    { id: "contribution_margin", name: "contribution_margin", label: "Contribution Margin", synonyms: ["margin", "cm"] },
    { id: "revenue", name: "revenue", label: "Revenue", synonyms: ["sales"] },
  ];

  it("flags synonyms shared by several metrics", () => {
    expect(findSynonymConflicts(metrics)).toEqual([{ term: "margin", metricIds: ["contribution_margin", "gross_margin"] }]);
  });

  it("derives glossary candidates from explicit candidates or conflicts", () => {
    const conflicts = findSynonymConflicts(metrics);
    expect(termCandidates({ id: "t", term: "Margin", definition: "" }, conflicts)).toEqual(["contribution_margin", "gross_margin"]);
    expect(termCandidates({ id: "t", term: "Sales", definition: "" }, conflicts)).toEqual([]);
    expect(termCandidates({ id: "t", term: "x", definition: "", candidate_metric_ids: ["a", "b"] }, conflicts)).toEqual(["a", "b"]);
  });
});

describe("driver trees", () => {
  const edges: DriverEdge[] = [
    { parent: "revenue", child: "orders", relation: "multiplicative", approved: true },
    { parent: "revenue", child: "aov", relation: "multiplicative", approved: true },
    { parent: "orders", child: "customers", relation: "multiplicative", approved: false, suggested: true },
  ];

  it("builds depth-first rows from the root", () => {
    expect(buildTreeRows("revenue", edges).map((r) => `${r.depth}:${r.metric}`)).toEqual(["0:revenue", "1:orders", "2:customers", "1:aov"]);
  });

  it("is safe against cycles and reports disconnected edges", () => {
    const rows = buildTreeRows("a", [
      { parent: "a", child: "b", relation: "additive", approved: true },
      { parent: "b", child: "a", relation: "additive", approved: true },
    ]);
    expect(rows.at(-1)).toMatchObject({ metric: "a", cycle: true });
    expect(orphanEdges("revenue", [...edges, { parent: "cogs", child: "units", relation: "additive", approved: true }])).toHaveLength(1);
  });

  it("merges suggestions as unapproved without overwriting existing edges", () => {
    const merged = mergeSuggestions(edges.slice(0, 1), [
      { parent: "revenue", child: "orders", relation: "additive", approved: true },
      { parent: "revenue", child: "aov", relation: "multiplicative", approved: true },
    ]);
    expect(merged).toHaveLength(2);
    expect(merged[0].relation).toBe("multiplicative");
    expect(merged[1]).toMatchObject({ child: "aov", approved: false, suggested: true });
  });

  it("renders approved and suggested edges distinctly, with approve/reject only on suggestions", async () => {
    const onDecide = vi.fn();
    const user = userEvent.setup();
    render(
      <TooltipProvider>
        <MetricTreeEditor
          root="revenue"
          edges={edges}
          metrics={[
            { id: "revenue", label: "Revenue" },
            { id: "orders", label: "Orders" },
            { id: "aov", label: "Average order value" },
            { id: "customers", label: "Customers" },
          ]}
          onDecide={onDecide}
          onRemove={vi.fn()}
          onAdd={vi.fn()}
        />
      </TooltipProvider>,
    );
    const items = screen.getAllByRole("treeitem");
    expect(items).toHaveLength(4);
    const suggested = items.find((i) => within(i).queryByText("Customers"))!;
    expect(suggested).toHaveAttribute("data-suggested", "true");
    expect(within(suggested).getByText("Suggested")).toBeInTheDocument();
    const approved = items.find((i) => within(i).queryByText("Orders"))!;
    expect(approved).toHaveAttribute("data-suggested", "false");
    expect(within(approved).queryByText("Suggested")).toBeNull();
    expect(within(approved).queryByRole("button", { name: /Approve/ })).toBeNull();
    expect(screen.getAllByRole("button", { name: /^Approve/ })).toHaveLength(1);
    await user.click(within(suggested).getByRole("button", { name: /^Approve/ }));
    expect(onDecide).toHaveBeenCalledWith(edges[2], "approve");
    expect(items[0]).toHaveAttribute("aria-level", "1");
    expect(suggested).toHaveAttribute("aria-level", "3");
  });

  it("adds a driver edge through the form", async () => {
    const onAdd = vi.fn();
    const user = userEvent.setup();
    render(
      <TooltipProvider>
        <MetricTreeEditor
          root="revenue"
          edges={[]}
          metrics={[
            { id: "revenue", label: "Revenue" },
            { id: "orders", label: "Orders" },
          ]}
          onAdd={onAdd}
        />
      </TooltipProvider>,
    );
    await user.selectOptions(screen.getByLabelText("Driver (child)"), "orders");
    await user.click(screen.getByRole("button", { name: /Add driver/ }));
    expect(onAdd).toHaveBeenCalledWith({ parent: "revenue", child: "orders", relation: "multiplicative", approved: true, suggested: false });
  });
});

describe("model reachability", () => {
  const model = {
    entities: [],
    dimensions: [],
    glossary: [],
    metric_trees: [],
    calendar: { fiscal_year_start_month: 1, week_start: "monday" },
    metrics: [
      { id: "revenue", name: "revenue", label: "Revenue", kind: "simple", entity: "order_line", format: "currency" },
      { id: "orders", name: "orders", label: "Orders", kind: "simple", entity: "order", format: "integer" },
      { id: "aov", name: "aov", label: "AOV", kind: "ratio", numerator: "revenue", denominator: "orders", format: "currency" },
    ],
    relationships: [
      { from_entity: "order_line", from_col: "order_id", to_entity: "order", to_col: "order_id", cardinality: "many_to_one", approved: true },
      { from_entity: "order", from_col: "customer_id", to_entity: "customer", to_col: "customer_id", cardinality: "many_to_one", approved: true },
      { from_entity: "order", from_col: "rep_id", to_entity: "rep", to_col: "rep_id", cardinality: "many_to_one", approved: false },
    ],
  } as unknown as SemanticModelSpec;

  it("follows ratio inputs to base entities", () => {
    expect(metricBaseEntities(model, "aov").sort()).toEqual(["order", "order_line"]);
  });

  it("only reaches entities through approved many-to-one relationships", () => {
    expect(reachableEntities(model, ["order_line"]).sort()).toEqual(["customer", "order", "order_line"]);
  });
});
