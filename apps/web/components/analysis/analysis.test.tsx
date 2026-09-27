import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Statement, StatementTypeBadge, normalizeStatementType } from "./statement";
import { EvidenceBadge } from "./evidence";
import { FilterChips, normalizeFilterContext } from "./filter-chips";
import { TooltipProvider } from "@/components/ui/tooltip";

describe("statement types", () => {
  it("renders each statement type with a distinct label and marker", () => {
    render(
      <div>
        <Statement type="observation">Revenue decreased 11.8%.</Statement>
        <Statement type="supported_explanation">Dallas accounted for 54% of the decline.</Statement>
        <Statement type="hypothesis">May be related to reduced marketing.</Statement>
      </div>,
    );
    expect(screen.getByText("Observation")).toBeInTheDocument();
    expect(screen.getByText("Supported explanation")).toBeInTheDocument();
    expect(screen.getByText("Hypothesis")).toBeInTheDocument();
    const hyp = screen.getByText("May be related to reduced marketing.").closest("p")!;
    expect(hyp.className).toMatch(/italic/);
    expect(within(hyp).getByText(/not confirmed/)).toHaveClass("sr-only");
    const obs = screen.getByText("Revenue decreased 11.8%.").closest("p")!;
    expect(obs.className).not.toMatch(/italic/);
  });

  it("never renders an unknown type as a hypothesis or explanation", () => {
    expect(normalizeStatementType("something")).toBe("observation");
    expect(normalizeStatementType("explanation")).toBe("supported_explanation");
    render(<StatementTypeBadge type="weird" />);
    expect(screen.getByText("Observation").closest("[data-statement-type]")).toHaveAttribute("data-statement-type", "observation");
  });
});

describe("EvidenceBadge", () => {
  it("shows the category and reveals reasons on click, with no numeric confidence", async () => {
    const user = userEvent.setup();
    render(
      <TooltipProvider>
        <EvidenceBadge strength="strong" reasons={["Effect is large (54% of change)", "Reproduced across two tests"]} />
      </TooltipProvider>,
    );
    const btn = screen.getByRole("button", { name: /Strong evidence/ });
    expect(btn.textContent).not.toMatch(/\d+%/);
    await user.click(btn);
    expect(await screen.findByText("Reproduced across two tests")).toBeInTheDocument();
  });

  it("maps unknown strengths to hypothesis only", () => {
    render(<EvidenceBadge strength="unknown" interactive={false} />);
    expect(screen.getByText("Hypothesis only")).toBeInTheDocument();
  });
});

describe("FilterChips", () => {
  it("states explicitly when no filters apply", () => {
    render(<FilterChips items={[]} />);
    expect(screen.getByText("No filters: all data")).toBeInTheDocument();
  });

  it("renders context items and raw filter specs", () => {
    render(
      <FilterChips
        items={[
          { label: "Date", value: "Aug 1 – 31, 2026", kind: "time" },
          { dimension: "region", op: "in", values: ["Dallas", "Houston"] },
          { dimension: "channel", op: "neq", values: ["Web"] },
        ]}
      />,
    );
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("DateAug 1 – 31, 2026");
    expect(items[1]).toHaveTextContent("RegionDallas, Houston");
    expect(items[2]).toHaveTextContent("Channel≠ Web");
  });

  it("supports removing a chip", async () => {
    const onRemove = vi.fn();
    const user = userEvent.setup();
    render(<FilterChips items={[{ label: "Region", value: "Dallas" }]} onRemove={onRemove} />);
    await user.click(screen.getByRole("button", { name: "Remove filter Region Dallas" }));
    expect(onRemove).toHaveBeenCalledWith(0);
  });

  it("normalizes long value lists", () => {
    const [c] = normalizeFilterContext([{ dimension: "sku", op: "in", values: ["a", "b", "c", "d", "e"] }]);
    expect(c.value).toBe("a, b, c +2");
  });
});
