import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { HomeSummary } from "@/lib/api/types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/w/ws1",
}));

const home = vi.fn<() => Promise<Partial<HomeSummary>>>();
vi.mock("@/lib/api/endpoints", async (orig) => {
  const actual = await orig<typeof import("@/lib/api/endpoints")>();
  return {
    ...actual,
    workspaces: {
      ...actual.workspaces,
      get: vi.fn(async () => ({ id: "ws1", name: "Summit Supply Co.", role: "owner" })),
      home: () => home(),
    },
  };
});

import { HomeView, exampleQuestions } from "./home-view";
import { WorkspaceProvider } from "@/components/providers/workspace";
import { TooltipProvider } from "@/components/ui/tooltip";

function renderHome() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <WorkspaceProvider id="ws1">
          <HomeView />
        </WorkspaceProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("Home", () => {
  it("shows onboarding with the demo loader when the workspace has no data", async () => {
    home.mockResolvedValue({ datasets: [] });
    renderHome();
    expect(await screen.findByRole("button", { name: /Load Summit Supply demo/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Upload a file/ })).toHaveAttribute("href", "/w/ws1/data?upload=1");
    expect(screen.getByRole("link", { name: /Connect a database/ })).toHaveAttribute("href", "/w/ws1/data?connect=1");
    expect(screen.getByRole("list", { name: "How AnalystOS works" }).children).toHaveLength(5);
  });

  it("renders the summary sections when data exists", async () => {
    home.mockResolvedValue({
      datasets: [{ id: "d1", name: "Orders", table_name: "orders", row_count: 1200, profile_status: "ready", issue_count: 2 }],
      recent_investigations: [],
      recent_findings: [
        {
          id: "f1",
          statement: "Dallas accounted for 54% of the decline",
          statement_type: "supported_explanation",
          evidence_strength: "strong",
          evidence_reasons: [],
          status: "confirmed",
          filter_context: [],
          artifact_ids: [],
          version_no: 1,
          created_at: "2026-09-01T00:00:00Z",
        },
      ],
      changes: [{ metric_id: "revenue", label: "Revenue", format: "currency", period: "August 2026", current: 88, baseline: 100, pct_change: -0.12 }],
    });
    renderHome();
    expect(await screen.findByText("Dallas accounted for 54% of the decline")).toBeInTheDocument();
    expect(screen.getByText("Supported")).toBeInTheDocument();
    expect(screen.getByLabelText("Ask a business question")).toBeInTheDocument();
    expect(screen.getByText("What changed")).toBeInTheDocument();
    expect(screen.getByText(/1 profile issue|2 profile issues/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Why did revenue fall in August 2026?" })).toBeInTheDocument();
  });

  it("shows the plan contradiction with an Investigate why that asks the stakeholder question", async () => {
    const base = {
      format: "currency",
      plan_role: "plan",
      period: "Q2 2026",
      window_start: "2026-04-01",
      window_end: "2026-07-01",
      higher_is_better: true,
      tolerance: 0.02,
      sql_actual: "SELECT 1",
      sql_plan: "SELECT 1",
    };
    home.mockResolvedValue({
      datasets: [{ id: "d1", name: "Orders", table_name: "orders", row_count: 1200, profile_status: "ready", issue_count: 0 }],
      recent_investigations: [],
      recent_findings: [],
      changes: [],
      plan_note: "Q2 2026 (the latest closed quarter) against plan.",
      plan_variances: [
        { ...base, metric_id: "revenue", label: "Revenue", stakeholder_term: "sales", plan_metric_id: "plan_revenue", plan_label: "Plan Revenue", actual: 1003, plan: 1000, variance: 3, variance_pct: 0.003, status: "on_plan" },
        { ...base, metric_id: "operating_profit", label: "Operating Profit", stakeholder_term: "net profit", plan_metric_id: "plan_operating_profit", plan_label: "Plan Operating Profit", actual: 53, plan: 100, variance: -47, variance_pct: -0.47, status: "unfavorable" },
      ],
    });
    renderHome();
    const note = await screen.findByRole("note", { name: "Plan contradiction" });
    expect(note).toHaveTextContent("Revenue finished on plan in Q2 2026 (+0.3%), but Operating Profit missed plan by 47.0%");
    expect(screen.getByRole("button", { name: /Investigate why/ })).toHaveAttribute(
      "title",
      "We made our sales number last quarter, but we missed our target for net profit. What happened?",
    );
    const tiles = screen.getAllByTestId("plan-tile");
    expect(tiles.map((t) => t.getAttribute("data-status"))).toEqual(["on_plan", "unfavorable"]);
    expect(screen.getByText("Against plan · Q2 2026")).toBeInTheDocument();
  });

  it("surfaces an error state when the summary fails", async () => {
    home.mockRejectedValue(new Error("boom"));
    renderHome();
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});

describe("exampleQuestions", () => {
  it("puts the largest moves first and dedupes", () => {
    const qs = exampleQuestions([
      { metric_id: "a", label: "Orders", current: 1, baseline: 1, pct_change: 0.01 },
      { metric_id: "b", label: "Gross Margin", current: 1, baseline: 1, pct_change: -0.2, period: "Aug" },
    ]);
    expect(qs[0]).toBe("Why did gross margin fall in Aug?");
    expect(qs).toHaveLength(4);
    expect(new Set(qs).size).toBe(4);
  });
});
