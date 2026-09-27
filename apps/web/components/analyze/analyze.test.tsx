import * as React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type {
  AnalysisOut,
  AnomalyResult,
  CorrelationResult,
  ForecastResult,
  TestResult,
} from "@/lib/api/resources/analysis";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/w/ws1/analyze",
}));
vi.mock("@/components/charts/echart", () => ({
  EChart: ({ ariaLabel }: { ariaLabel: string }) => <div role="img" aria-label={ariaLabel} />,
  useThemeVersion: () => 0,
}));
vi.mock("@/lib/api/endpoints", async (orig) => {
  const actual = await orig<typeof import("@/lib/api/endpoints")>();
  return {
    ...actual,
    workspaces: { ...actual.workspaces, get: vi.fn(async () => ({ id: "ws1", name: "WS", role: "owner" })) },
    metrics: { ...actual.metrics, list: vi.fn(async () => []) },
  };
});

import {
  anomalyOption,
  backtestRows,
  correlationOption,
  defaultSeriesWindow,
  forecastOption,
  formatP,
  pickModel,
} from "./charts";
import { seriesSource } from "./sources";
import { sensitivityParam } from "./anomaly-panel";
import { ForecastView } from "./forecast-panel";
import { StatsView } from "./stats-panel";
import { CorrelationView } from "./correlation-panel";
import { WorkspaceProvider } from "@/components/providers/workspace";

const forecast: ForecastResult = {
  history: [
    { timestamp: "2026-05-01", value: 100 },
    { timestamp: "2026-06-01", value: 110 },
    { timestamp: "2026-07-01", value: 120 },
  ],
  horizon: 2,
  frequency: "MS",
  seasonal_period: null,
  interval: 0.8,
  models: [
    {
      name: "naive",
      params: {},
      points: [
        { timestamp: "2026-08-01", mean: 120, lower: 100, upper: 140 },
        { timestamp: "2026-09-01", mean: 120, lower: 95, upper: 145 },
      ],
      backtest: { folds: [{ origin: "2026-06-01", horizon: 1, mae: 12, mape: 0.1, coverage: 1 }], mae: 12, mape: 0.1, coverage: 1 },
      aic: null,
      error: null,
    },
    {
      name: "ets",
      params: {},
      points: [
        { timestamp: "2026-08-01", mean: 130, lower: 118, upper: 142 },
        { timestamp: "2026-09-01", mean: 140, lower: 122, upper: 158 },
      ],
      backtest: { folds: [{ origin: "2026-06-01", horizon: 1, mae: 5, mape: 0.04, coverage: 0.5 }], mae: 5, mape: 0.04, coverage: 0.5 },
      aic: 10,
      error: null,
    },
    { name: "arima", params: {}, points: [], backtest: null, aic: null, error: "needs at least 8 observations" },
    { name: "seasonal_naive", params: {}, points: [{ timestamp: "2026-08-01", mean: 1, lower: 0, upper: 2 }], backtest: null, aic: null, error: null, backtest_note: "not backtested: needs 26 points (two seasons of 12); the series has 3" },
  ],
  best_model: "ets",
  selection_metric: "backtest MAE",
  notes: [],
};

const out = <R,>(kind: AnalysisOut["kind"], result: R, extra: Partial<AnalysisOut<R>> = {}): AnalysisOut<R> => ({
  id: "art1",
  kind,
  method: "m",
  title: "T",
  label: "Model estimate",
  exploratory: false,
  summary: "Computed summary.",
  assumptions: [],
  caveats: [],
  notes: [],
  params: {},
  sql: "select 1",
  input_row_count: 3,
  input_truncated: false,
  metric_versions: { revenue: "revenue@v1:abc" },
  dataset_versions: [{ table: "orders", content_hash: "deadbeefcafe", row_count: 3 }],
  filter_context: [{ label: "Date", value: "2026-05-01 to 2026-07-31", kind: "time" }],
  result,
  table: null,
  created_at: "2026-09-18T10:00:00Z",
  ...extra,
});

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <WorkspaceProvider id="ws1">{ui}</WorkspaceProvider>
    </QueryClientProvider>,
  );
}

describe("analysis helpers", () => {
  it("defaults to complete buckets before the current month, half-open, per grain", () => {
    const today = new Date("2026-09-18T12:00:00Z");
    expect(defaultSeriesWindow(today)).toEqual({ start: "2024-09-01", end: "2026-09-01" });
    // 2026-09-01 is a Tuesday: the last full Monday-start week ends before Monday 2026-08-31.
    expect(defaultSeriesWindow(today, "week")).toEqual({ start: "2024-09-02", end: "2026-08-31" });
    expect(defaultSeriesWindow(today, "day")).toEqual({ start: "2026-06-01", end: "2026-09-01" });
    expect(defaultSeriesWindow(today, "quarter")).toEqual({ start: "2024-07-01", end: "2026-07-01" });
  });
  it("sends an inclusive end date as the half-open end and a filter only when values are given", () => {
    const d = { metricId: "revenue", start: "2026-01-01", end: "2026-08-31", grain: "month" as const, filterDimension: "branch", filterValues: "Dallas, Houston" };
    expect(seriesSource(d)).toEqual({
      metric_id: "revenue",
      start: "2026-01-01",
      end: "2026-09-01",
      grain: "month",
      filters: [{ dimension: "branch", op: "in", values: ["Dallas", "Houston"] }],
    });
    expect(seriesSource({ ...d, filterValues: " " }).filters).toEqual([]);
  });
  it("ranks backtests by MAE with failed models last and flags the best", () => {
    const rows = backtestRows(forecast);
    expect(rows.map((r) => r.name)).toEqual(["ets", "naive", "seasonal_naive", "arima"]);
    expect(rows[2].note).toMatch(/not backtested: needs 26 points/);
    expect(rows[0].best).toBe(true);
    expect(rows[3].error).toMatch(/at least 8/);
  });
  it("picks the requested model, else the best, never a failed one", () => {
    expect(pickModel(forecast, "naive")?.name).toBe("naive");
    expect(pickModel(forecast, "arima")?.name).toBe("ets");
    expect(pickModel(forecast, null)?.name).toBe("ets");
  });
  it("draws history, an interval band (upper − lower stacked on lower) and a joined forecast line", () => {
    const opt = forecastOption(forecast, "ets", "currency");
    const series = opt.series as { name: string; data: (number | null)[]; stack?: string }[];
    expect((opt.xAxis as { data: string[] }).data).toEqual(["2026-05-01", "2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"]);
    expect(series[0].data).toEqual([100, 110, 120]);
    expect(series[1].data).toEqual([null, null, null, 118, 122]);
    expect(series[2].data).toEqual([null, null, null, 24, 36]);
    expect(series[1].stack).toBe("band");
    expect(series[3].data).toEqual([null, null, 120, 130, 140]);
  });
  it("marks anomalies and change points", () => {
    const r: AnomalyResult = {
      points: [
        { timestamp: "2026-08-11", value: 10, expected: 10, lower: 8, upper: 12, score: 0, is_anomaly: false, direction: null },
        { timestamp: "2026-08-12", value: 2, expected: 10, lower: 8, upper: 12, score: -5, is_anomaly: true, direction: "down" },
      ],
      anomalies: [],
      change_points: [{ index: 1, timestamp: "2026-08-12", mean_before: 10, mean_after: 2, magnitude: -8, pct_change: -0.8 }],
      method: "seasonal robust z",
      sensitivity: "medium",
      threshold: 3,
      seasonal_period: 7,
      window: 28,
      frequency: "D",
      notes: [],
    };
    const opt = anomalyOption(r, "currency");
    const series = opt.series as { name: string; data: unknown[]; markLine?: { data: unknown[] } }[];
    expect(series.find((s) => s.name === "Anomaly")!.data).toEqual([null, 2]);
    expect(series.find((s) => s.name === "Expected range")!.data).toEqual([4, 4]);
    expect(series.find((s) => s.name === "Actual")!.markLine!.data).toEqual([{ xAxis: "2026-08-12" }]);
  });
  it("builds a correlation heatmap over the matrix", () => {
    const r: CorrelationResult = {
      method: "pearson",
      columns: ["a", "b"],
      matrix: [
        [1, 0.456],
        [0.456, 1],
      ],
      pairs: [],
      exploratory: true,
      caveat: "Correlation is not causation.",
    };
    const opt = correlationOption(r);
    expect((opt.series as { data: unknown[] }[])[0].data).toContainEqual([1, 0, 0.46]);
  });
  it("maps sensitivity presets and custom thresholds", () => {
    expect(sensitivityParam({ preset: "high" })).toBe("high");
    expect(sensitivityParam({ z: 3.5 })).toBe(3.5);
  });
  it("formats p-values without false precision", () => {
    expect(formatP(0.00001)).toBe("< 0.001");
    expect(formatP(0.04321)).toBe("0.043");
    expect(formatP(null)).toBe("n/a");
  });
});

describe("analysis views", () => {
  it("shows the forecast chart, backtest table and forecast intervals; clicking a model switches the forecast", async () => {
    wrap(<ForecastView out={out("forecast", forecast)} format="number" />);
    expect(screen.getByRole("img", { name: /Forecast with prediction interval/ })).toBeInTheDocument();
    const bt = screen.getByRole("table", { name: "Backtest accuracy" });
    const rows = within(bt).getAllByRole("row");
    expect(rows[1]).toHaveTextContent(/ETS.*best/);
    expect(within(bt).getByText(/Not fitted: needs at least 8/)).toBeInTheDocument();
    const fc = screen.getByRole("table", { name: "Forecast values" });
    expect(within(fc).getAllByRole("row")[1]).toHaveTextContent("2026-08-01");
    expect(within(fc).getAllByRole("row")[1]).toHaveTextContent("130");
    await userEvent.click(within(bt).getByText("Naive (last value)"));
    expect(within(screen.getByRole("table", { name: "Forecast values" })).getAllByRole("row")[1]).toHaveTextContent("120");
    // Provenance: summary, versions, SQL toggle, lineage link.
    expect(screen.getByTestId("analysis-summary")).toHaveTextContent("Computed summary.");
    expect(screen.getByText("revenue@v1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Lineage/ })).toHaveAttribute("href", "/w/ws1/lineage/artifact/art1");
  });

  it("shows a statistical test with its assumption checks", () => {
    const t: TestResult = {
      test: "welch_t",
      statistic: 2.1,
      p_value: 0.04,
      df: 30.2,
      alpha: 0.05,
      significant: true,
      effect_size: 0.5,
      effect_size_name: "Cohen's d",
      estimate: 3,
      ci_low: 0.1,
      ci_high: 5.9,
      confidence: 0.95,
      n: { a: 20, b: 22 },
      assumptions: [],
      interpretation: "Group A's mean is higher.",
      caveats: [],
    };
    wrap(
      <StatsView
        out={out("stats_test", t, {
          label: "Statistical test",
          assumptions: [
            { name: "Normality (group A)", passed: false, detail: "Shapiro p = 0.01" },
            { name: "Independence", passed: null, detail: "Not testable from the data" },
          ],
        })}
      />,
    );
    expect(screen.getByText(/Significant at α = 0.05/)).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Assumption checks" });
    expect(within(list).getByText("Normality (group A)")).toBeInTheDocument();
    expect(within(list).getByLabelText("violated")).toBeInTheDocument();
    expect(within(list).getByLabelText("not checked")).toBeInTheDocument();
    expect(screen.getByText("Cohen's d")).toBeInTheDocument();
  });

  it("labels correlation results exploratory", () => {
    const r: CorrelationResult = {
      method: "pearson",
      columns: ["a", "b"],
      matrix: [
        [1, 0.8],
        [0.8, 1],
      ],
      pairs: [{ a: "a", b: "b", r: 0.8, p_value: 0.0001, n: 50, strength: "strong" }],
      exploratory: true,
      caveat: "Correlation is not causation.",
    };
    wrap(<CorrelationView out={out("correlation", r, { label: "Exploratory", exploratory: true })} />);
    expect(screen.getByTestId("analysis-label")).toHaveTextContent("Exploratory");
    expect(screen.getByRole("note")).toHaveTextContent(/association in the data, not a cause/);
    expect(within(screen.getByRole("table", { name: "Correlation pairs" })).getByText("< 0.001")).toBeInTheDocument();
  });
});
