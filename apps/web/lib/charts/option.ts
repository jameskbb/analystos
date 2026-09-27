/** Builds ECharts options from a ChartConfig + tabular result. Styling follows the app tokens. */
import type { EChartsOption } from "echarts";
import type { ChartConfig, QueryResult } from "@/lib/api/types";
import { formatCompact, formatValue, toNumber } from "@/lib/format";
import {
  boxStats,
  colIndex,
  heatmapCells,
  histogram,
  sortRows,
  toSeries,
  uniqueCategories,
  waterfallSteps,
} from "./transform";

export interface ChartPalette {
  series: string[];
  fg: string;
  fgMuted: string;
  fgSubtle: string;
  border: string;
  bg: string;
  bgMuted: string;
  positive: string;
  negative: string;
  accent: string;
  accentSoft: string;
  fontFamily: string;
}

export const LIGHT_PALETTE: ChartPalette = {
  series: ["#2f5fb3", "#c46a1b", "#2e8a6e", "#8a4fb0", "#b4372f", "#5f7187", "#a88a1c", "#3b9bb8"],
  fg: "#18181b",
  fgMuted: "#52525b",
  fgSubtle: "#71717a",
  border: "#e4e4e7",
  bg: "#ffffff",
  bgMuted: "#f0f0f2",
  positive: "#1f7a4d",
  negative: "#b4372f",
  accent: "#2f5fb3",
  accentSoft: "#eaf0fa",
  fontFamily: "ui-sans-serif, -apple-system, 'Segoe UI', Inter, sans-serif",
};

/** Reads the live CSS tokens so charts follow light/dark theme. */
export function readPalette(): ChartPalette {
  if (typeof window === "undefined") return LIGHT_PALETTE;
  const cs = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback;
  return {
    series: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => v(`--chart-${i}`, LIGHT_PALETTE.series[i - 1])),
    fg: v("--fg", LIGHT_PALETTE.fg),
    fgMuted: v("--fg-muted", LIGHT_PALETTE.fgMuted),
    fgSubtle: v("--fg-subtle", LIGHT_PALETTE.fgSubtle),
    border: v("--border", LIGHT_PALETTE.border),
    bg: v("--bg", LIGHT_PALETTE.bg),
    bgMuted: v("--bg-muted", LIGHT_PALETTE.bgMuted),
    positive: v("--positive", LIGHT_PALETTE.positive),
    negative: v("--negative", LIGHT_PALETTE.negative),
    accent: v("--accent", LIGHT_PALETTE.accent),
    accentSoft: v("--accent-soft", LIGHT_PALETTE.accentSoft),
    fontFamily: LIGHT_PALETTE.fontFamily,
  };
}

export function axisStyle(p: ChartPalette) {
  return {
    axisLine: { lineStyle: { color: p.border } },
    axisTick: { show: false },
    axisLabel: { color: p.fgSubtle, fontSize: 11, hideOverlap: true },
    splitLine: { lineStyle: { color: p.border, type: "dashed" as const } },
    nameTextStyle: { color: p.fgSubtle, fontSize: 11 },
  };
}

export function base(p: ChartPalette, showLegend: boolean): EChartsOption {
  return {
    color: p.series,
    backgroundColor: "transparent",
    textStyle: { fontFamily: p.fontFamily, color: p.fgMuted, fontSize: 11 },
    animationDuration: 250,
    grid: { left: 8, right: 16, top: showLegend ? 32 : 14, bottom: 8, containLabel: true },
    legend: showLegend
      ? {
          type: "scroll",
          top: 0,
          left: 0,
          icon: "roundRect",
          itemWidth: 10,
          itemHeight: 3,
          textStyle: { color: p.fgMuted, fontSize: 11 },
          pageIconColor: p.fgMuted,
        }
      : { show: false },
    tooltip: {
      trigger: "axis",
      backgroundColor: p.bg,
      borderColor: p.border,
      textStyle: { color: p.fg, fontSize: 12 },
      extraCssText: "box-shadow: 0 4px 16px rgb(0 0 0 / 0.12); border-radius: 4px;",
      axisPointer: { type: "line", lineStyle: { color: p.fgSubtle, type: "dashed" } },
    },
  };
}

export function buildChartOption(config: ChartConfig, result: QueryResult, p: ChartPalette = LIGHT_PALETTE): EChartsOption {
  const { columns } = result;
  const rows = sortRows(columns, result.rows, config.sort ?? null);
  const fmt = config.format ?? undefined;
  const valueFormatter = (v: unknown) => formatValue(v, fmt);
  const axisFmt = (v: number) => formatCompact(v, fmt);
  const y = (config.y ?? []).filter((c) => colIndex(columns, c) >= 0);
  const x = config.x && colIndex(columns, config.x) >= 0 ? config.x : columns[0]?.name;

  switch (config.type) {
    case "line":
    case "area":
    case "bar":
    case "stacked_bar": {
      const ys = y.length ? y : columns.filter((c) => c.name !== x).slice(0, 1).map((c) => c.name);
      const { categories, series } = toSeries(columns, rows, x ?? "", ys, config.series);
      const horizontal = (config.type === "bar" || config.type === "stacked_bar") && !!config.horizontal;
      const stacked = config.type === "stacked_bar" || (config.type === "area" && series.length > 1);
      const opt = base(p, series.length > 1);
      const catAxis = {
        type: "category" as const,
        data: horizontal ? [...categories].reverse() : categories,
        ...axisStyle(p),
        splitLine: { show: false },
        boundaryGap: config.type === "bar" || config.type === "stacked_bar",
        axisLabel: { ...axisStyle(p).axisLabel, width: horizontal ? 140 : undefined, overflow: "truncate" as const },
      };
      const valAxis = {
        type: "value" as const,
        ...axisStyle(p),
        axisLabel: { ...axisStyle(p).axisLabel, formatter: axisFmt },
      };
      opt.xAxis = horizontal ? valAxis : catAxis;
      opt.yAxis = horizontal ? catAxis : valAxis;
      opt.tooltip = { ...(opt.tooltip as object), valueFormatter } as EChartsOption["tooltip"];
      opt.series = series.map((s) => ({
        name: s.name,
        type: config.type === "line" || config.type === "area" ? "line" : "bar",
        data: horizontal ? [...s.data].reverse() : s.data,
        stack: stacked ? "total" : undefined,
        smooth: false,
        showSymbol: categories.length <= 24,
        symbolSize: 4,
        lineStyle: { width: 1.75 },
        areaStyle: config.type === "area" ? { opacity: series.length > 1 ? 0.55 : 0.18 } : undefined,
        barMaxWidth: 36,
        itemStyle: { borderRadius: stacked ? 0 : horizontal ? [0, 2, 2, 0] : [2, 2, 0, 0] },
        emphasis: { focus: "series" },
      })) as EChartsOption["series"];
      if (categories.length > 60 && !horizontal) {
        opt.dataZoom = [{ type: "inside" }, { type: "slider", height: 14, bottom: 0, borderColor: p.border }];
        opt.grid = { ...(opt.grid as object), bottom: 26 };
      }
      return opt;
    }

    case "scatter": {
      const xi = colIndex(columns, x);
      const yi = colIndex(columns, y[0] ?? columns[1]?.name);
      const si = colIndex(columns, config.series);
      const groups = new Map<string, [number, number][]>();
      for (const r of rows) {
        const a = toNumber(r[xi]);
        const b = toNumber(r[yi]);
        if (a === null || b === null) continue;
        const k = si >= 0 ? String(r[si] ?? "(null)") : columns[yi]?.name ?? "value";
        const g = groups.get(k);
        if (g) g.push([a, b]);
        else groups.set(k, [[a, b]]);
      }
      const opt = base(p, groups.size > 1);
      opt.tooltip = {
        trigger: "item",
        backgroundColor: p.bg,
        borderColor: p.border,
        textStyle: { color: p.fg, fontSize: 12 },
        formatter: (params: unknown) => {
          const d = (params as { value: [number, number]; seriesName: string }).value;
          return `${columns[xi]?.name}: ${formatValue(d[0])}<br/>${columns[yi]?.name}: ${formatValue(d[1])}`;
        },
      };
      opt.xAxis = { type: "value", name: columns[xi]?.name, nameLocation: "middle", nameGap: 24, scale: true, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: (v: number) => formatCompact(v) } };
      opt.yAxis = { type: "value", name: columns[yi]?.name, scale: true, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: (v: number) => formatCompact(v) } };
      opt.grid = { ...(opt.grid as object), bottom: 24 };
      opt.series = [...groups.entries()].map(([name, data]) => ({
        name,
        type: "scatter",
        data,
        symbolSize: data.length > 2000 ? 3 : 6,
        itemStyle: { opacity: 0.7 },
        large: data.length > 2000,
      })) as EChartsOption["series"];
      return opt;
    }

    case "histogram": {
      const field = x && colIndex(columns, x) >= 0 && columns[colIndex(columns, x)] ? x : y[0];
      const vi = colIndex(columns, config.value ?? y[0] ?? field);
      const values = rows.map((r) => toNumber(r[vi])).filter((v): v is number => v !== null);
      const { edges, counts } = histogram(values);
      const labels = counts.map((_, i) => `${formatCompact(edges[i], fmt)}–${formatCompact(edges[i + 1], fmt)}`);
      const opt = base(p, false);
      opt.xAxis = { type: "category", data: labels, ...axisStyle(p), splitLine: { show: false }, name: columns[vi]?.name, nameLocation: "middle", nameGap: 26 };
      opt.yAxis = { type: "value", name: "Rows", ...axisStyle(p) };
      opt.grid = { ...(opt.grid as object), bottom: 24 };
      opt.series = [{ type: "bar", data: counts, barCategoryGap: "4%", itemStyle: { color: p.series[0] } }];
      return opt;
    }

    case "box": {
      const ci = colIndex(columns, x !== y[0] ? x : null);
      const vi = colIndex(columns, y[0] ?? config.value);
      const stats = boxStats(rows, ci, vi);
      const opt = base(p, false);
      opt.tooltip = {
        trigger: "item",
        backgroundColor: p.bg,
        borderColor: p.border,
        textStyle: { color: p.fg, fontSize: 12 },
        formatter: (params: unknown) => {
          const prm = params as { seriesType: string; dataIndex: number; value: number[] };
          if (prm.seriesType === "scatter") return `Outlier: ${formatValue(prm.value[1], fmt)}`;
          const s = stats[prm.dataIndex];
          return [
            `<b>${s.category}</b> (n=${s.n})`,
            `Max (whisker): ${formatValue(s.max, fmt)}`,
            `Q3: ${formatValue(s.q3, fmt)}`,
            `Median: ${formatValue(s.median, fmt)}`,
            `Q1: ${formatValue(s.q1, fmt)}`,
            `Min (whisker): ${formatValue(s.min, fmt)}`,
          ].join("<br/>");
        },
      };
      opt.xAxis = { type: "category", data: stats.map((s) => s.category), ...axisStyle(p), splitLine: { show: false } };
      opt.yAxis = { type: "value", scale: true, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: axisFmt } };
      opt.series = [
        {
          type: "boxplot",
          data: stats.map((s) => [s.min, s.q1, s.median, s.q3, s.max]),
          itemStyle: { color: p.accentSoft, borderColor: p.series[0], borderWidth: 1.25 },
        },
        {
          type: "scatter",
          data: stats.flatMap((s, i) => s.outliers.map((o) => [i, o])),
          symbolSize: 4,
          itemStyle: { color: p.fgSubtle, opacity: 0.6 },
        },
      ] as EChartsOption["series"];
      return opt;
    }

    case "heatmap": {
      const xi = colIndex(columns, x);
      const yi = colIndex(columns, config.series ?? columns.find((c) => c.name !== x)?.name);
      const vi = colIndex(columns, config.value ?? y[0]);
      const { xs, ys, cells, min, max } = heatmapCells(rows, xi, yi, vi);
      const opt = base(p, false);
      opt.tooltip = {
        trigger: "item",
        backgroundColor: p.bg,
        borderColor: p.border,
        textStyle: { color: p.fg, fontSize: 12 },
        formatter: (params: unknown) => {
          const v = (params as { value: [number, number, number] }).value;
          return `${xs[v[0]]} · ${ys[v[1]]}<br/><b>${formatValue(v[2], fmt)}</b>`;
        },
      };
      opt.grid = { left: 8, right: 16, top: 8, bottom: 44, containLabel: true };
      opt.xAxis = { type: "category", data: xs, ...axisStyle(p), splitArea: { show: false }, splitLine: { show: false } };
      opt.yAxis = { type: "category", data: ys, ...axisStyle(p), splitLine: { show: false } };
      const diverging = min < 0 && max > 0;
      const bound = Math.max(Math.abs(min), Math.abs(max));
      opt.visualMap = {
        min: diverging ? -bound : min,
        max: diverging ? bound : max,
        calculable: true,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        itemHeight: 120,
        itemWidth: 10,
        textStyle: { color: p.fgSubtle, fontSize: 10 },
        formatter: (v: unknown) => formatCompact(v, fmt),
        inRange: { color: diverging ? [p.negative, p.bgMuted, p.positive] : [p.bgMuted, p.series[0]] },
      } as EChartsOption["visualMap"];
      opt.series = [
        {
          type: "heatmap",
          data: cells,
          label: { show: xs.length * ys.length <= 80, fontSize: 10, color: p.fg, formatter: (prm: unknown) => formatCompact((prm as { value: number[] }).value[2], fmt) },
          itemStyle: { borderColor: p.bg, borderWidth: 1 },
        },
      ] as EChartsOption["series"];
      return opt;
    }

    case "waterfall": {
      const xi = colIndex(columns, x);
      const yi = colIndex(columns, y[0] ?? config.value);
      const labels = rows.map((r) => String(r[xi] ?? "(null)"));
      const values = rows.map((r) => toNumber(r[yi]));
      const steps = waterfallSteps(labels, values);
      const opt = base(p, false);
      opt.tooltip = {
        trigger: "axis",
        backgroundColor: p.bg,
        borderColor: p.border,
        textStyle: { color: p.fg, fontSize: 12 },
        axisPointer: { type: "shadow" },
        formatter: (params: unknown) => {
          const arr = params as { dataIndex: number }[];
          const s = steps[arr[0]?.dataIndex ?? 0];
          const sign = s.kind === "delta" && s.value > 0 ? "+" : "";
          return `<b>${s.label}</b><br/>${sign}${formatValue(s.value, fmt)}`;
        },
      };
      opt.xAxis = { type: "category", data: steps.map((s) => s.label), ...axisStyle(p), splitLine: { show: false }, axisLabel: { ...axisStyle(p).axisLabel, interval: 0, rotate: steps.length > 8 ? 30 : 0 } };
      opt.yAxis = { type: "value", ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: axisFmt }, scale: false };
      opt.series = [
        {
          type: "bar",
          stack: "wf",
          silent: true,
          itemStyle: { color: "transparent", borderColor: "transparent" },
          data: steps.map((s) => s.base),
        },
        {
          type: "bar",
          stack: "wf",
          barMaxWidth: 40,
          label: {
            show: steps.length <= 14,
            position: "top",
            fontSize: 10,
            color: p.fgMuted,
            formatter: (prm: unknown) => {
              const s = steps[(prm as { dataIndex: number }).dataIndex];
              const sign = s.kind === "delta" && s.value > 0 ? "+" : s.kind === "delta" && s.value < 0 ? "−" : "";
              return `${sign}${formatCompact(Math.abs(s.value), fmt)}`;
            },
          },
          data: steps.map((s) => ({
            value: Math.abs(s.value),
            itemStyle: {
              color: s.kind !== "delta" ? p.fgSubtle : s.value >= 0 ? p.positive : p.negative,
              borderRadius: 1,
            },
          })),
        },
      ] as EChartsOption["series"];
      return opt;
    }

    case "kpi":
    case "table":
    default: {
      // Rendered as HTML by ChartView; return a minimal option for completeness.
      const cats = x ? uniqueCategories(rows, colIndex(columns, x)) : [];
      return { ...base(p, false), xAxis: { type: "category", data: cats }, yAxis: { type: "value" }, series: [] };
    }
  }
}
