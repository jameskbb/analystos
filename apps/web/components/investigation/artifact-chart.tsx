"use client";
import * as React from "react";
import type { EChartsOption } from "echarts";
import { ImageDown, ScanSearch } from "lucide-react";
import type { ArtifactRecord, ChartProvenance, FilterContextItem } from "@/lib/api/types";
import { EChart, type EChartHandle, useThemeVersion } from "@/components/charts/echart";
import { ChartView } from "@/components/charts/chart-view";
import { ChartInspector } from "@/components/charts/chart-inspector";
import { FilterChips } from "@/components/analysis/filter-chips";
import { Button } from "@/components/ui/button";
import { readPalette, type ChartPalette } from "@/lib/charts/option";
import { formatCompact } from "@/lib/format";
import { artifactResult, datasetVersions, metricVersions } from "./model";

type Obj = Record<string, unknown>;
const asArray = (v: unknown): Obj[] => (Array.isArray(v) ? (v as Obj[]) : v && typeof v === "object" ? [v as Obj] : []);

/** Applies the app's chart styling to an investigator ECharts option without changing its data. */
export function styleChartSpec(spec: Obj, p: ChartPalette): EChartsOption {
  const axis = (a: Obj): Obj => ({
    ...a,
    axisLine: { lineStyle: { color: p.border } },
    axisTick: { show: false },
    axisLabel: {
      color: p.fgSubtle,
      fontSize: 11,
      hideOverlap: true,
      ...(a.type === "value" ? { formatter: (v: number) => formatCompact(v) } : {}),
      ...((a.axisLabel as Obj) ?? {}),
    },
    splitLine: { lineStyle: { color: p.border, type: "dashed" } },
  });
  const series = asArray(spec.series).map((s) => ({ barMaxWidth: 40, ...s }));
  const out: Obj = {
    ...spec,
    title: undefined,
    color: p.series,
    backgroundColor: "transparent",
    textStyle: { fontFamily: p.fontFamily, color: p.fgMuted, fontSize: 11 },
    grid: { left: 8, right: 16, top: series.length > 1 ? 30 : 14, bottom: 8, containLabel: true, ...((spec.grid as Obj) ?? {}) },
    tooltip: {
      trigger: "axis",
      ...((spec.tooltip as Obj) ?? {}),
      backgroundColor: p.bg,
      borderColor: p.border,
      textStyle: { color: p.fg, fontSize: 12 },
    },
    legend: series.length > 1 ? { top: 0, left: 0, textStyle: { color: p.fgMuted, fontSize: 11 }, ...((spec.legend as Obj) ?? {}) } : { show: false },
    series,
  };
  if (spec.xAxis) out.xAxis = Array.isArray(spec.xAxis) ? asArray(spec.xAxis).map(axis) : axis(spec.xAxis as Obj);
  if (spec.yAxis) out.yAxis = Array.isArray(spec.yAxis) ? asArray(spec.yAxis).map(axis) : axis(spec.yAxis as Obj);
  return out as EChartsOption;
}

export function artifactProvenance(a: ArtifactRecord, filters: FilterContextItem[]): ChartProvenance {
  const mv = metricVersions(a);
  return {
    datasets: datasetVersions(a).map((d) => d.table),
    metric: a.metric_ids?.[0] ?? mv[0]?.[0] ?? null,
    metric_version: mv[0]?.[1] ?? null,
    dimensions: typeof a.params?.dimension === "string" ? [a.params.dimension] : [],
    filters: a.filter_context?.length ? a.filter_context : filters,
    calculation: typeof a.params?.method === "string" ? String(a.params.method) : null,
    sql: a.sql ?? null,
    python: a.python ?? null,
    artifact_id: a.id,
  };
}

/** Renders an artifact's chart: its recorded ECharts spec when present, else its result via ChartView. */
export function ArtifactChart({
  artifact,
  filters,
  height = 240,
}: {
  artifact: ArtifactRecord;
  filters: FilterContextItem[];
  height?: number;
}) {
  const [inspect, setInspect] = React.useState(false);
  const ref = React.useRef<EChartHandle>(null);
  const themeVersion = useThemeVersion();
  const provenance = artifactProvenance(artifact, filters);
  const title = artifact.title || (artifact.chart_spec as { title?: { text?: string } } | null)?.title?.text || "Chart";
  const option = React.useMemo(
    () => (artifact.chart_spec ? styleChartSpec(artifact.chart_spec as Obj, readPalette()) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [artifact.chart_spec, themeVersion],
  );
  const result = artifactResult(artifact);

  if (!option) {
    if (!result) return null;
    return <ChartView result={result} provenance={provenance} title={title} height={height} />;
  }
  const exportPng = () => {
    const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() || "#fff";
    const url = ref.current?.toDataURL(bg);
    if (!url) return;
    const a = document.createElement("a");
    a.href = url;
    a.download = `${title.replace(/[^\w.-]+/g, "_")}.png`;
    a.click();
  };
  return (
    <figure className="flex min-w-0 flex-col" data-print-avoid-break>
      <div className="flex items-center gap-1 pb-1">
        <figcaption className="min-w-0 flex-1 truncate text-xs font-medium">{title}</figcaption>
        <Button variant="ghost" size="icon-xs" onClick={exportPng} aria-label="Download chart as PNG" className="no-print">
          <ImageDown />
        </Button>
        <Button variant="ghost" size="xs" onClick={() => setInspect(true)} className="no-print">
          <ScanSearch /> Inspect
        </Button>
      </div>
      <div style={{ height }}>
        <EChart ref={ref} option={option} ariaLabel={`Chart: ${title}`} />
      </div>
      <FilterChips items={provenance.filters} size="xs" className="pt-1.5" />
      <ChartInspector
        open={inspect}
        onOpenChange={setInspect}
        config={{ type: "bar", title }}
        provenance={provenance}
        title={title}
        reason="Chart recorded by the investigation engine alongside the executed query."
      />
    </figure>
  );
}
