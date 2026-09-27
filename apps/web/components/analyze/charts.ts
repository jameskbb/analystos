/** Pure ECharts option builders and table shapers for the Analyze screens (unit-tested). */
import type { EChartsOption } from "echarts";
import { axisStyle, base, LIGHT_PALETTE, type ChartPalette } from "@/lib/charts/option";
import { formatCompact, formatValue } from "@/lib/format";
import type {
  AnomalyResult,
  CorrelationResult,
  ForecastModelName,
  ForecastResult,
  ModelForecast,
  QuadrantResult,
} from "@/lib/api/resources/analysis";

export const MODEL_LABEL: Record<ForecastModelName, string> = {
  naive: "Naive (last value)",
  seasonal_naive: "Seasonal naive",
  ets: "Exponential smoothing (ETS)",
  arima: "ARIMA",
};

const day = (t: string) => String(t).slice(0, 10);

const iso = (d: Date) => d.toISOString().slice(0, 10);

/**
 * Default analysis window per grain, half-open and ending at a complete bucket before the
 * reference date's month so the last point is never a partial period:
 * month → 24 months, week → 104 Monday-start weeks, day → 92 days, quarter → 8 quarters.
 */
export function defaultSeriesWindow(today: Date, grain: "day" | "week" | "month" | "quarter" = "month"): { start: string; end: string } {
  const monthStart = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1));
  if (grain === "week") {
    // Last Monday on or before the first of the month: every week before it is complete.
    const end = new Date(monthStart);
    end.setUTCDate(end.getUTCDate() - ((end.getUTCDay() + 6) % 7));
    const start = new Date(end);
    start.setUTCDate(start.getUTCDate() - 7 * 104);
    return { start: iso(start), end: iso(end) };
  }
  if (grain === "day") {
    const start = new Date(monthStart);
    start.setUTCDate(start.getUTCDate() - 92);
    return { start: iso(start), end: iso(monthStart) };
  }
  if (grain === "quarter") {
    const end = new Date(Date.UTC(today.getUTCFullYear(), Math.floor(today.getUTCMonth() / 3) * 3, 1));
    const start = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() - 24, 1));
    return { start: iso(start), end: iso(end) };
  }
  const start = new Date(Date.UTC(monthStart.getUTCFullYear(), monthStart.getUTCMonth() - 24, 1));
  return { start: iso(start), end: iso(monthStart) };
}

export interface BacktestRow {
  name: ForecastModelName;
  label: string;
  mae: number | null;
  mape: number | null;
  coverage: number | null;
  folds: number;
  best: boolean;
  error: string | null;
  note: string | null;
}

/** One row per model, ranked by backtest MAE (models that failed to fit last). */
export function backtestRows(r: ForecastResult): BacktestRow[] {
  const rows = r.models.map((m) => ({
    name: m.name,
    label: MODEL_LABEL[m.name] ?? m.name,
    mae: m.backtest?.mae ?? null,
    mape: m.backtest?.mape ?? null,
    coverage: m.backtest?.coverage ?? null,
    folds: m.backtest?.folds.length ?? 0,
    best: m.name === r.best_model,
    error: m.error,
    note: m.backtest ? null : (m.backtest_note ?? null),
  }));
  const key = (x: BacktestRow) => (x.error ? Number.POSITIVE_INFINITY : (x.mae ?? Number.MAX_VALUE));
  return rows.sort((a, b) => key(a) - key(b));
}

export function pickModel(r: ForecastResult, name: ForecastModelName | null): ModelForecast | null {
  const usable = r.models.filter((m) => !m.error && m.points.length);
  return usable.find((m) => m.name === name) ?? usable.find((m) => m.name === r.best_model) ?? usable[0] ?? null;
}

/**
 * History as a solid line, the chosen model's forecast dashed, and its prediction interval as a
 * shaded band (lower as an invisible base, upper − lower stacked on top).
 */
export function forecastOption(
  r: ForecastResult,
  modelName: ForecastModelName | null,
  format: string | null,
  p: ChartPalette = LIGHT_PALETTE,
): EChartsOption {
  const m = pickModel(r, modelName);
  const hist = r.history.map((h) => [day(h.timestamp), h.value] as [string, number | null]);
  const fc = m?.points ?? [];
  const cats = [...hist.map((h) => h[0]), ...fc.map((f) => day(f.timestamp))];
  const n = hist.length;
  const pad = <X>(xs: X[], before: number) => [...Array<null>(before).fill(null), ...xs];
  // The forecast line starts at the last actual so the two lines join.
  const last = hist[n - 1]?.[1] ?? null;
  const fcMean = n ? [...Array<null>(n - 1).fill(null), last, ...fc.map((f) => f.mean)] : fc.map((f) => f.mean);
  const bandLabel = `${Math.round(r.interval * 100)}% interval`;
  const opt: EChartsOption = {
    ...base(p, true),
    xAxis: { type: "category", data: cats, boundaryGap: false, ...axisStyle(p) },
    yAxis: {
      type: "value",
      scale: true,
      ...axisStyle(p),
      axisLabel: { ...axisStyle(p).axisLabel, formatter: (v: number) => formatCompact(v, format) },
    },
    tooltip: {
      ...(base(p, true).tooltip as object),
      valueFormatter: (v: unknown) => (v === null || v === undefined ? "n/a" : formatValue(v, format)),
    },
    series: [
      {
        name: "Actual",
        type: "line",
        data: pad(
          hist.map((h) => h[1]),
          0,
        ),
        showSymbol: false,
        lineStyle: { width: 2, color: p.series[0] },
        itemStyle: { color: p.series[0] },
      },
      {
        name: "band-base",
        type: "line",
        data: pad(
          fc.map((f) => f.lower),
          n,
        ),
        stack: "band",
        lineStyle: { opacity: 0 },
        showSymbol: false,
        silent: true,
        tooltip: { show: false },
      },
      {
        name: bandLabel,
        type: "line",
        data: pad(
          fc.map((f) => f.upper - f.lower),
          n,
        ),
        stack: "band",
        lineStyle: { opacity: 0 },
        showSymbol: false,
        areaStyle: { color: p.series[1], opacity: 0.18 },
        itemStyle: { color: p.series[1] },
        silent: true,
        tooltip: { show: false },
      },
      {
        name: m ? `Forecast: ${MODEL_LABEL[m.name] ?? m.name}` : "Forecast",
        type: "line",
        data: fcMean,
        showSymbol: false,
        lineStyle: { width: 2, type: "dashed", color: p.series[1] },
        itemStyle: { color: p.series[1] },
      },
    ],
  };
  (opt.legend as { data?: string[] }).data = ["Actual", bandLabel, m ? `Forecast: ${MODEL_LABEL[m.name] ?? m.name}` : "Forecast"];
  return opt;
}

/** Values with the expected band, flagged anomalies as points, and change points as vertical rules. */
export function anomalyOption(r: AnomalyResult, format: string | null, p: ChartPalette = LIGHT_PALETTE): EChartsOption {
  const cats = r.points.map((x) => day(x.timestamp));
  const lower = r.points.map((x) => x.lower);
  const width = r.points.map((x) => (x.upper !== null && x.lower !== null ? x.upper - x.lower : null));
  return {
    ...base(p, true),
    xAxis: { type: "category", data: cats, boundaryGap: false, ...axisStyle(p) },
    yAxis: {
      type: "value",
      scale: true,
      ...axisStyle(p),
      axisLabel: { ...axisStyle(p).axisLabel, formatter: (v: number) => formatCompact(v, format) },
    },
    tooltip: {
      ...(base(p, true).tooltip as object),
      valueFormatter: (v: unknown) => (v === null || v === undefined ? "n/a" : formatValue(v, format)),
    },
    series: [
      { name: "band-base", type: "line", data: lower, stack: "band", lineStyle: { opacity: 0 }, showSymbol: false, silent: true, tooltip: { show: false } },
      {
        name: "Expected range",
        type: "line",
        data: width,
        stack: "band",
        lineStyle: { opacity: 0 },
        showSymbol: false,
        areaStyle: { color: p.fgSubtle, opacity: 0.14 },
        itemStyle: { color: p.fgSubtle },
        silent: true,
        tooltip: { show: false },
      },
      {
        name: "Actual",
        type: "line",
        data: r.points.map((x) => x.value),
        showSymbol: false,
        lineStyle: { width: 1.5, color: p.series[0] },
        itemStyle: { color: p.series[0] },
        markLine: r.change_points.length
          ? {
              symbol: "none",
              silent: true,
              lineStyle: { color: p.fgMuted, type: "dotted" },
              label: { formatter: "shift", color: p.fgSubtle, fontSize: 10 },
              data: r.change_points.map((c) => ({ xAxis: day(c.timestamp) })),
            }
          : undefined,
      },
      {
        name: "Anomaly",
        type: "scatter",
        symbolSize: 8,
        data: r.points.map((x) => (x.is_anomaly ? x.value : null)),
        itemStyle: {
          color: (params: { dataIndex: number }) =>
            r.points[params.dataIndex]?.direction === "up" ? p.positive : p.negative,
        },
      },
    ],
    legend: { ...(base(p, true).legend as object), data: ["Actual", "Expected range", "Anomaly"] },
  };
}

/** Growth vs profitability scatter with the quadrant thresholds drawn. */
export function quadrantOption(r: QuadrantResult, p: ChartPalette = LIGHT_PALETTE): EChartsOption {
  const quadrants = [...new Set(r.rows.map((x) => x.quadrant))];
  return {
    ...base(p, true),
    tooltip: {
      trigger: "item",
      backgroundColor: p.bg,
      borderColor: p.border,
      textStyle: { color: p.fg, fontSize: 12 },
      formatter: (params: unknown) => {
        const d = (params as { data: [number, number, string, number | null] }).data;
        return `${d[2]}<br/>growth ${formatValue(d[0], "percent")} · profitability ${formatValue(d[1], "percent")}`;
      },
    },
    xAxis: { type: "value", name: "Growth", ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: (v: number) => formatValue(v, "percent") } },
    yAxis: { type: "value", name: "Profitability", ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: (v: number) => formatValue(v, "percent") } },
    series: quadrants.map((q, i) => ({
      name: q,
      type: "scatter" as const,
      symbolSize: 9,
      itemStyle: { color: p.series[i % p.series.length] },
      data: r.rows.filter((x) => x.quadrant === q && x.growth !== null && x.profitability !== null).map((x) => [x.growth, x.profitability, x.item, x.volume]),
      markLine:
        i === 0
          ? {
              symbol: "none",
              silent: true,
              lineStyle: { color: p.fgSubtle, type: "dashed" },
              label: { show: false },
              data: [{ xAxis: r.growth_threshold }, { yAxis: r.profitability_threshold }],
            }
          : undefined,
    })),
  };
}

/** Correlation matrix heatmap on a diverging −1..1 scale. */
export function correlationOption(r: CorrelationResult, p: ChartPalette = LIGHT_PALETTE): EChartsOption {
  const data: [number, number, number | null][] = [];
  r.matrix.forEach((row, i) => row.forEach((v, j) => data.push([j, i, v === null ? null : Math.round(v * 100) / 100])));
  return {
    ...base(p, false),
    grid: { left: 8, right: 16, top: 8, bottom: 48, containLabel: true },
    tooltip: {
      trigger: "item",
      backgroundColor: p.bg,
      borderColor: p.border,
      textStyle: { color: p.fg, fontSize: 12 },
      formatter: (params: unknown) => {
        const d = (params as { data: [number, number, number | null] }).data;
        return `${r.columns[d[1]]} × ${r.columns[d[0]]}: r = ${d[2] ?? "n/a"}`;
      },
    },
    xAxis: { type: "category", data: r.columns, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, rotate: 30 } },
    yAxis: { type: "category", data: r.columns, ...axisStyle(p) },
    visualMap: {
      min: -1,
      max: 1,
      calculable: false,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      itemHeight: 120,
      textStyle: { color: p.fgSubtle, fontSize: 10 },
      inRange: { color: [p.negative, p.bgMuted, p.accent] },
    },
    series: [{ type: "heatmap", data, label: { show: r.columns.length <= 8, fontSize: 10, color: p.fg } }],
  };
}

/** p-value text that never shows false precision. */
export function formatP(p: number | null | undefined): string {
  if (p === null || p === undefined || Number.isNaN(p)) return "n/a";
  if (p < 0.001) return "< 0.001";
  return p.toFixed(3);
}
