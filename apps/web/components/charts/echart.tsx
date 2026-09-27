"use client";
import * as React from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, ScatterChart, BoxplotChart, HeatmapChart, GraphChart, TreeChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  VisualMapComponent,
  MarkLineComponent,
  TitleComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsOption } from "echarts";
import { cn } from "@/lib/utils";

echarts.use([
  BarChart,
  LineChart,
  ScatterChart,
  BoxplotChart,
  HeatmapChart,
  GraphChart,
  TreeChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  VisualMapComponent,
  MarkLineComponent,
  TitleComponent,
  CanvasRenderer,
]);

export interface EChartHandle {
  /** PNG data URL rendered at 2× on the current theme background. */
  toDataURL: (background?: string) => string | null;
  instance: () => echarts.ECharts | null;
}

/** Thin React wrapper around an ECharts instance with resize handling and theme-change re-render. */
export const EChart = React.forwardRef<
  EChartHandle,
  {
    option: EChartsOption;
    className?: string;
    style?: React.CSSProperties;
    ariaLabel: string;
    onEvents?: Record<string, (params: unknown) => void>;
  }
>(function EChart({ option, className, style, ariaLabel, onEvents }, ref) {
  const el = React.useRef<HTMLDivElement>(null);
  const chart = React.useRef<echarts.ECharts | null>(null);

  React.useEffect(() => {
    if (!el.current) return;
    const inst = echarts.init(el.current, undefined, { renderer: "canvas" });
    chart.current = inst;
    const ro = new ResizeObserver(() => inst.resize({ animation: { duration: 0 } }));
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      inst.dispose();
      chart.current = null;
    };
  }, []);

  React.useEffect(() => {
    chart.current?.setOption(option, { notMerge: true, lazyUpdate: true });
  }, [option]);

  React.useEffect(() => {
    const inst = chart.current;
    if (!inst || !onEvents) return;
    Object.entries(onEvents).forEach(([evt, fn]) => inst.on(evt, fn));
    return () => {
      Object.keys(onEvents).forEach((evt) => inst.off(evt));
    };
  }, [onEvents]);

  React.useImperativeHandle(ref, () => ({
    toDataURL: (background) =>
      chart.current?.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: background ?? "#ffffff" }) ?? null,
    instance: () => chart.current,
  }));

  return <div ref={el} role="img" aria-label={ariaLabel} className={cn("h-full w-full", className)} style={style} />;
});

/** Re-renders consumers when the theme changes so charts pick up new CSS tokens. */
export function useThemeVersion(): number {
  const [v, setV] = React.useState(0);
  React.useEffect(() => {
    const on = () => setV((x) => x + 1);
    window.addEventListener("aos-theme-change", on);
    return () => window.removeEventListener("aos-theme-change", on);
  }, []);
  return v;
}
