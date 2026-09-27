import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { computePivot, partialAggregatesFor, type PivotValue } from "@/lib/pivot";
import { PivotTable, headerSpans } from "./pivot-table";

const raw = {
  columns: ["region", "channel", "revenue"],
  rows: [
    ["Dallas", "Web", 100],
    ["Dallas", "Branch", 30],
    ["Houston", "Web", 20],
    ["Houston", "Branch", 80],
  ],
};

describe("PivotTable", () => {
  it("renders column headers, cells, a total column and a grand total row", () => {
    const values: PivotValue[] = [{ field: "revenue", agg: "sum", format: "integer" }];
    const pivot = computePivot(raw, { rows: ["region"], cols: ["channel"], values });
    render(<PivotTable pivot={pivot} rowFields={["region"]} colFields={["channel"]} values={values} />);
    const table = screen.getByRole("table", { name: "Pivot table" });
    expect(within(table).getByRole("columnheader", { name: "region" })).toBeInTheDocument();
    expect(within(table).getByText("Total")).toBeInTheDocument();
    const rows = within(table).getAllByRole("row");
    const dallas = rows.find((r) => r.textContent?.startsWith("Dallas"))!;
    expect(dallas).toHaveTextContent("Dallas30100130");
    const grand = rows.find((r) => r.getAttribute("data-row-kind") === "grand")!;
    expect(grand).toHaveTextContent("Grand total110120230");
  });

  it("styles subtotal rows for nested row dimensions and suppresses repeated labels", () => {
    const values: PivotValue[] = [{ field: "revenue", agg: "sum" }];
    const pivot = computePivot(raw, { rows: ["region", "channel"], cols: [], values });
    const { container } = render(<PivotTable pivot={pivot} rowFields={["region", "channel"]} colFields={[]} values={values} />);
    const subtotals = container.querySelectorAll('tr[data-row-kind="subtotal"]');
    expect(subtotals).toHaveLength(2);
    expect(subtotals[0]).toHaveTextContent("Dallas total130");
    const leaves = container.querySelectorAll('tr[data-row-kind="leaf"]');
    expect(leaves[0].querySelectorAll("th")[0]).toHaveTextContent("Dallas");
    expect(leaves[1].querySelectorAll("th")[0]).toHaveTextContent("");
  });

  it("leaves non-additive distinct-count subtotals blank and explains why", () => {
    const values: PivotValue[] = [{ field: "customer", agg: "count_distinct" }];
    const aggs = partialAggregatesFor(values);
    const pivot = computePivot(
      {
        mode: "partial",
        columns: ["region", "channel", ...aggs.map((a) => a.alias)],
        rows: [
          ["Dallas", "Web", 3],
          ["Dallas", "Branch", 2],
        ],
      },
      { rows: ["region", "channel"], cols: [], values },
    );
    const { container } = render(<PivotTable pivot={pivot} rowFields={["region", "channel"]} colFields={[]} values={values} />);
    const sub = container.querySelector('tr[data-row-kind="subtotal"]')!;
    const cell = sub.querySelector("td:last-child")!;
    expect(cell).toHaveTextContent("");
    expect(cell).toHaveAttribute("title", expect.stringMatching(/not additive/));
    expect(screen.getByText(/Distinct counts are not additive/)).toBeInTheDocument();
  });

  it("groups multi-level column headers", () => {
    expect(headerSpans([["2025", "Q1"], ["2025", "Q2"], ["2026", "Q1"]], 0)).toEqual([
      { label: "2025", span: 2 },
      { label: "2026", span: 1 },
    ]);
    expect(headerSpans([["2025", "Q1"], ["2025", "Q2"]], 1)).toEqual([
      { label: "Q1", span: 1 },
      { label: "Q2", span: 1 },
    ]);
  });
});
