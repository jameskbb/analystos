import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DetectedTablePreview, SheetPicker, groupBySheet } from "./sheet-picker";
import type { DetectedTable, FileInspectionResult } from "@/lib/api/resources/data";

const table = (p: Partial<DetectedTable>): DetectedTable => ({
  key: "k",
  sheet: "Branch budgets",
  name_suggestion: "budgets_branch",
  header_row: 3,
  first_data_row: 4,
  last_data_row: 40,
  range: "A3:F40",
  columns: [
    { name: "branch", original_name: "Branch", inferred_type: "string" },
    { name: "budget_usd", original_name: "Budget ($)", inferred_type: "float", nonconforming_count: 2, nonconforming_examples: ["n/a"] },
  ],
  row_count: 37,
  preview_rows: [["Dallas", 1200000]],
  skipped_rows: [
    { row: 1, reason: "title", content: "Summit Supply Co. FY2026 Budget" },
    { row: 2, reason: "blank", content: "" },
  ],
  title: "Summit Supply Co. FY2026 Budget",
  ...p,
});

const inspection: FileInspectionResult = {
  file_name: "budgets.xlsx",
  format: "excel",
  size_bytes: 1000,
  sheets: [
    { name: "Branch budgets", table_count: 1 },
    { name: "Category budgets", table_count: 2 },
    { name: "Notes", empty: true, visible: false },
  ],
  tables: [
    table({ key: "b1" }),
    table({ key: "c1", sheet: "Category budgets", name_suggestion: "category_budgets", title: null }),
    table({ key: "c2", sheet: "Category budgets", name_suggestion: "category_budget_notes", title: null, header_row: null }),
  ],
  warnings: [],
};

describe("SheetPicker", () => {
  it("groups detected tables by sheet and keeps empty/hidden sheets visible", () => {
    expect(groupBySheet(inspection).map((g) => g.tables.length)).toEqual([1, 2, 0]);
    render(<SheetPicker inspection={inspection} focusedKey="b1" onFocus={() => {}} selected={new Set(["b1"])} onToggle={() => {}} />);
    expect(screen.getByText("Branch budgets")).toBeInTheDocument();
    expect(screen.getByText("Hidden")).toBeInTheDocument();
    expect(screen.getByText("No data on this sheet.")).toBeInTheDocument();
    expect(screen.getByText(/no header/)).toBeInTheDocument();
    expect(screen.getAllByText(/A3:F40 · 37 rows · 2 cols · header row 3/)).toHaveLength(2);
  });

  it("focuses and toggles tables", async () => {
    const user = userEvent.setup();
    const onFocus = vi.fn();
    const onToggle = vi.fn();
    render(<SheetPicker inspection={inspection} focusedKey="b1" onFocus={onFocus} selected={new Set(["b1"])} onToggle={onToggle} />);
    await user.click(screen.getByRole("button", { name: /category_budgets/ }));
    expect(onFocus).toHaveBeenCalledWith("c1");
    await user.click(screen.getByRole("checkbox", { name: "Ingest category_budgets" }));
    expect(onToggle).toHaveBeenCalledWith("c1", true);
    expect(screen.getByRole("checkbox", { name: /Ingest Summit Supply/ })).toBeChecked();
  });
});

describe("DetectedTablePreview", () => {
  it("shows the detected header row, excluded title/blank rows and type warnings", () => {
    render(<DetectedTablePreview table={table({})} />);
    expect(screen.getByText("Header row").nextSibling).toHaveTextContent("3");
    const excluded = screen.getByText("Detected and excluded from the data").parentElement!;
    expect(within(excluded).getByText("Title row 1")).toBeInTheDocument();
    expect(within(excluded).getByText("Empty row 2")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Column warnings" })).toHaveTextContent("2 value(s) don't fit float (e.g. n/a)");
    expect(screen.getByText(/headers normalized/)).toBeInTheDocument();
  });
});
