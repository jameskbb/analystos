"use client";
import * as React from "react";
import type { ChartConfig, ChartProvenance, FilterContextItem, FilterSpec } from "@/lib/api/types";
import type { Tile, TileDataOut } from "@/lib/api/resources/outputs";
import { ChartView } from "@/components/charts/chart-view";
import { Kpi } from "@/components/charts/kpi";
import { EChart } from "@/components/charts/echart";
import { DataGrid } from "@/components/data-grid/data-grid";
import { FilterChips } from "@/components/analysis/filter-chips";
import { ErrorState, LoadingState } from "@/components/states/states";
import { Markdown } from "./markdown";

const COMPARE_LABEL: Record<string, string> = {
  pop: "vs prior period",
  yoy: "vs same period last year",
};

/** Provenance for the Chart Inspector, merging tile binding and the server's compiled query. */
export function tileProvenance(tile: Tile, data: TileDataOut | undefined, dashboardId?: string): ChartProvenance {
  const mq = tile.binding.metric_query;
  const mv = data?.provenance?.metric_versions ?? {};
  const firstMetric = mq?.metrics?.[0] ?? null;
  const v = firstMetric ? mv[firstMetric] : undefined;
  const version = typeof v === "object" && v !== null ? ((v as { version_no?: number }).version_no ?? null) : (v as number | undefined) ?? null;
  return {
    metric: firstMetric,
    metric_version: version,
    dimensions: mq?.dimensions ?? [],
    datasets: Object.keys(data?.provenance?.dataset_versions ?? {}),
    filters: (data?.filter_context ?? []) as FilterContextItem[],
    sql: data?.provenance?.sql ?? data?.compiled?.sql ?? data?.result?.sql ?? null,
    artifact_id: tile.binding.artifact_id ?? null,
    tile: dashboardId ? { dashboard_id: dashboardId, tile_id: tile.id } : null,
    calculation: mq
      ? "Compiled by the semantic layer from the metric definition (grain-safe joins, dashboard filters and date range applied)."
      : tile.binding.saved_query_id
        ? "Saved query executed as written."
        : null,
  };
}

/**
 * Renders a tile's body for its kind. Pure presentational: data fetching lives in the grid.
 */
export function TileBody({
  tile,
  data,
  loading,
  error,
  onRetry,
  dashboardId,
}: {
  dashboardId?: string;
  tile: Tile;
  data?: TileDataOut;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
}) {
  if (tile.kind === "text") {
    return (
      <div className="h-full overflow-auto px-3 py-2 scrollbar-thin" data-testid="tile-text">
        {tile.text?.trim() ? <Markdown>{tile.text}</Markdown> : <p className="text-sm text-fg-subtle italic">Empty text tile.</p>}
      </div>
    );
  }
  if (loading) return <LoadingState variant="block" label="Computing" className="min-h-0" />;
  if (error) return <ErrorState error={error} compact onRetry={onRetry} className="h-full py-2" />;
  if (data?.error) return <ErrorState error={new Error(data.error)} compact onRetry={onRetry} className="h-full py-2" />;
  if (!data) return null;
  const chips = (data.filter_context ?? []) as (FilterContextItem | FilterSpec)[];

  if (tile.kind === "kpi") {
    const kpi = data.kpi;
    const compare = tile.binding.compare ?? "pop";
    return (
      <div className="flex h-full flex-col justify-between gap-1 px-3 py-2" data-testid="tile-kpi">
        <Kpi
          label={kpi?.label ?? tile.title}
          value={kpi?.value ?? null}
          baseline={compare === "none" ? undefined : kpi?.baseline}
          pctChange={compare === "none" ? undefined : kpi?.pct_change}
          format={kpi?.format ?? tile.viz.format ?? null}
          comparisonLabel={COMPARE_LABEL[compare] ?? "vs baseline"}
        />
        <FilterChips items={chips} size="xs" />
      </div>
    );
  }

  const result = data.result;
  if (tile.kind === "table") {
    if (!result) return <p className="px-3 py-4 text-sm text-fg-subtle">No result.</p>;
    return (
      <div className="flex h-full min-h-0 flex-col gap-1 px-2 pb-2" data-testid="tile-table">
        <DataGrid columns={result.columns} rows={result.rows} className="min-h-0 flex-1 rounded border border-border" />
        <FilterChips items={chips} size="xs" />
      </div>
    );
  }

  // chart
  if (!result && data.chart?.option) {
    return (
      <div className="flex h-full min-h-0 flex-col gap-1 px-2 pb-2" data-testid="tile-chart" data-capture-key={tile.id} data-capture-title={tile.title}>
        <div className="min-h-0 flex-1">
          <EChart option={data.chart.option as never} ariaLabel={`Chart: ${tile.title}`} />
        </div>
        <FilterChips items={chips} size="xs" />
      </div>
    );
  }
  if (!result) return <p className="px-3 py-4 text-sm text-fg-subtle">No result.</p>;
  const config: ChartConfig | null = tile.viz.chart ?? (data.chart && data.chart.type ? (data.chart as ChartConfig) : null);
  return (
    <div className="flex h-full min-h-0 flex-col px-2 pb-2" data-testid="tile-chart" data-capture-key={tile.id} data-capture-title={tile.title}>
      <ChartView
        result={result}
        config={config}
        provenance={tileProvenance(tile, data, dashboardId)}
        title={tile.title}
        height="calc(100% - 58px)"
        className="h-full min-h-0"
        toolbar
        exportName={tile.title}
      />
    </div>
  );
}
