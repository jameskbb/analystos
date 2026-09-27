import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BlockView } from "./report-blocks";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { ReportBlockSpec } from "@/lib/api/resources/outputs";

const wrap = (ui: React.ReactNode) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );

describe("report BlockView", () => {
  it("renders the executive summary in separate, typed sections", () => {
    const block: ReportBlockSpec = {
      id: "s",
      type: "summary",
      observations: [{ text: "Revenue decreased 11.8%.", evidence_strength: "strong" }],
      supported_explanations: ["Dallas accounted for 54% of the decline."],
      hypotheses: ["Reduced marketing spend may have contributed."],
    };
    wrap(<BlockView block={block} />);
    const obs = screen.getByRole("region", { name: "Observations" });
    const exp = screen.getByRole("region", { name: "Supported explanations" });
    const hyp = screen.getByRole("region", { name: "Open hypotheses" });
    expect(within(obs).getByText("Revenue decreased 11.8%.")).toBeInTheDocument();
    expect(within(exp).getByText("Dallas accounted for 54% of the decline.")).toBeInTheDocument();
    expect(within(hyp).getByText("Reduced marketing spend may have contributed.").closest("p")!.className).toMatch(/italic/);
    expect(within(obs).queryByText(/Dallas/)).not.toBeInTheDocument();
    expect(within(obs).getByText("Strong evidence")).toBeInTheDocument();
  });

  it("says so when there are no confirmed findings", () => {
    wrap(<BlockView block={{ id: "s", type: "summary", observations: [], supported_explanations: [], hypotheses: [] }} />);
    expect(screen.getByText(/No confirmed findings yet/)).toBeInTheDocument();
  });

  it("renders a finding snapshot with its statement type and evidence", () => {
    wrap(
      <BlockView
        block={{
          id: "f",
          type: "finding",
          snapshot: {
            statement: "Dallas order volume fell 19.4%.",
            statement_type: "observation",
            evidence_strength: "moderate",
            status: "draft",
            filter_context: [{ label: "Region", value: "Dallas" }],
          },
        }}
      />,
    );
    expect(screen.getByText("Dallas order volume fell 19.4%.")).toBeInTheDocument();
    expect(screen.getByText("Moderate evidence")).toBeInTheDocument();
    expect(screen.getByText("Not a confirmed finding")).toBeInTheDocument();
    expect(screen.getByText("Dallas")).toBeInTheDocument();
  });

  it("renders open questions as hypotheses and sources as a list", () => {
    wrap(
      <>
        <BlockView block={{ id: "q", type: "open_questions", items: ["Why did Houston web conversion fall?"] }} />
        <BlockView block={{ id: "src", type: "sources", items: [{ kind: "metric", label: "Revenue", version: 3 }] }} />
      </>,
    );
    expect(screen.getByText("Why did Houston web conversion fall?").closest("[data-statement-type]")).toHaveAttribute("data-statement-type", "hypothesis");
    expect(screen.getByText("v3")).toBeInTheDocument();
  });
});
