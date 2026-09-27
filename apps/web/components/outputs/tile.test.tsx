import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Tile } from "@/lib/api/resources/outputs";
import { TileBody, tileProvenance } from "./tile";
import { TooltipProvider } from "@/components/ui/tooltip";

vi.mock("@/components/charts/chart-view", () => ({
  ChartView: ({ title, config }: { title?: string; config?: { type: string } | null }) => (
    <div data-testid="chart-view">
      {title}:{config?.type ?? "auto"}
    </div>
  ),
}));

const tile = (t: Partial<Tile>): Tile => ({ id: "t1", kind: "kpi", title: "Revenue", binding: {}, viz: {}, ...t });
const result = {
  columns: [
    { name: "region", type: "VARCHAR" },
    { name: "revenue", type: "DOUBLE" },
  ],
  rows: [
    ["Dallas", 1200],
    ["Houston", 900],
  ],
  row_count: 2,
  truncated: false,
  elapsed_ms: 3,
};

const wrap = (ui: React.ReactNode) => render(<TooltipProvider>{ui}</TooltipProvider>);

describe("TileBody", () => {
  it("renders a KPI with its comparison and filter context", () => {
    wrap(
      <TileBody
        tile={tile({ kind: "kpi", binding: { compare: "pop" } })}
        data={{
          tile_id: "t1",
          kpi: { value: 3_700_000, baseline: 4_200_000, pct_change: -0.119, format: "currency", label: "Revenue" },
          filter_context: [{ label: "Date", value: "Aug 1 – 31, 2026", kind: "time" }],
        }}
      />,
    );
    expect(screen.getByTestId("tile-kpi")).toHaveTextContent("$3,700,000");
    expect(screen.getByText("vs prior period (-$4.2M)".replace("-", ""), { exact: false })).toBeInTheDocument();
    expect(screen.getByText("Aug 1 – 31, 2026")).toBeInTheDocument();
  });

  it("omits the comparison when disabled", () => {
    wrap(<TileBody tile={tile({ binding: { compare: "none" } })} data={{ tile_id: "t1", kpi: { value: 5, baseline: 4, pct_change: 0.25 } }} />);
    expect(screen.queryByText(/vs prior period/)).not.toBeInTheDocument();
  });

  it("renders a table tile as a grid", () => {
    wrap(<TileBody tile={tile({ kind: "table" })} data={{ tile_id: "t1", result }} />);
    expect(screen.getByTestId("tile-table")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("No filters: all data")).toBeInTheDocument();
  });

  it("renders a chart tile through ChartView with the configured type", () => {
    wrap(<TileBody tile={tile({ kind: "chart", title: "By region", viz: { chart: { type: "bar", x: "region", y: ["revenue"] } } })} data={{ tile_id: "t1", result }} />);
    expect(screen.getByTestId("chart-view")).toHaveTextContent("By region:bar");
  });

  it("renders text tiles as markdown without fetching", () => {
    wrap(<TileBody tile={tile({ kind: "text", text: "## Notes\n\nDallas is **down**." })} />);
    expect(screen.getByRole("heading", { name: "Notes" })).toBeInTheDocument();
    expect(screen.getByText("down").tagName).toBe("STRONG");
  });

  it("shows loading and error states", () => {
    const { rerender } = wrap(<TileBody tile={tile({ kind: "chart" })} loading />);
    expect(screen.getByText(/Computing/)).toBeInTheDocument();
    rerender(
      <TooltipProvider>
        <TileBody tile={tile({ kind: "chart" })} data={{ tile_id: "t1", error: "grain_error: fan-out join" }} />
      </TooltipProvider>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("fan-out join");
  });

  it("builds inspector provenance from the binding and server response", () => {
    const p = tileProvenance(
      tile({ kind: "chart", binding: { metric_query: { metrics: ["revenue"], dimensions: ["region"] } } }),
      { tile_id: "t1", provenance: { sql: "select 1", metric_versions: { revenue: { version_no: 4 } }, dataset_versions: { orders: {} } } },
    );
    expect(p).toMatchObject({ metric: "revenue", metric_version: 4, dimensions: ["region"], datasets: ["orders"], sql: "select 1" });
    expect(p.tile).toBeNull();
  });

  it("points the inspector at the tile lineage when the dashboard is known (R-30)", () => {
    const p = tileProvenance(tile({ kind: "kpi" }), undefined, "d1");
    expect(p.tile).toEqual({ dashboard_id: "d1", tile_id: "t1" });
  });
});
