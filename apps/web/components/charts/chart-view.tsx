"use client";
import * as React from "react";
import { toast } from "sonner";
import { ImageDown, ScanSearch, SlidersHorizontal } from "lucide-react";
import type { ChartConfig, ChartProvenance, ChartType, QueryResult } from "@/lib/api/types";
import { buildChartOption, readPalette } from "@/lib/charts/option";
import { ALL_CHART_TYPES, CHART_TYPE_LABELS, suggestCharts, type ChartSuggestion } from "@/lib/charts/suggest";
import { EChart, type EChartHandle, useThemeVersion } from "./echart";
import { ChartInspector } from "./chart-inspector";
import { Kpi } from "./kpi";
import { DataGrid } from "@/components/data-grid/data-grid";
import { FilterChips } from "@/components/analysis/filter-chips";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { NativeSelect } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { colIndex } from "@/lib/charts/transform";
import { toNumber } from "@/lib/format";

export function useChartSuggestions(result: QueryResult | null | undefined): ChartSuggestion[] {
  return React.useMemo(() => (result ? suggestCharts(result.columns, result.rows) : []), [result]);
}

/** Axis/series controls for a chart config. */
export function ChartConfigEditor({
  config,
  onChange,
  result,
}: {
  config: ChartConfig;
  onChange: (c: ChartConfig) => void;
  result: QueryResult;
}) {
  const cols = result.columns.map((c) => c.name);
  const set = (patch: Partial<ChartConfig>) => onChange({ ...config, ...patch });
  const id = React.useId();
  return (
    <div className="grid grid-cols-2 gap-2">
      <div className="col-span-2 flex flex-col gap-1">
        <Label htmlFor={`${id}-type`}>Chart type</Label>
        <NativeSelect id={`${id}-type`} value={config.type} onChange={(e) => set({ type: e.target.value as ChartType })}>
          {ALL_CHART_TYPES.map((t) => (
            <option key={t} value={t}>
              {CHART_TYPE_LABELS[t]}
            </option>
          ))}
        </NativeSelect>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${id}-x`}>{config.type === "scatter" ? "X measure" : "X / category"}</Label>
        <NativeSelect id={`${id}-x`} value={config.x ?? ""} onChange={(e) => set({ x: e.target.value || null })}>
          <option value="">(none)</option>
          {cols.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </NativeSelect>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${id}-y`}>{config.type === "heatmap" ? "Value" : "Y measure"}</Label>
        <NativeSelect
          id={`${id}-y`}
          value={config.y?.[0] ?? ""}
          onChange={(e) => set({ y: e.target.value ? [e.target.value, ...(config.y ?? []).slice(1).filter((v) => v !== e.target.value)] : [], value: e.target.value || null })}
        >
          <option value="">(none)</option>
          {cols.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </NativeSelect>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${id}-s`}>{config.type === "heatmap" ? "Y / rows" : "Series / color"}</Label>
        <NativeSelect id={`${id}-s`} value={config.series ?? ""} onChange={(e) => set({ series: e.target.value || null })}>
          <option value="">(none)</option>
          {cols.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </NativeSelect>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${id}-f`}>Number format</Label>
        <NativeSelect id={`${id}-f`} value={config.format ?? ""} onChange={(e) => set({ format: e.target.value || null })}>
          <option value="">Auto</option>
          <option value="currency">Currency</option>
          <option value="number">Number</option>
          <option value="integer">Integer</option>
          <option value="percent">Percent</option>
        </NativeSelect>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${id}-sort`}>Sort</Label>
        <NativeSelect
          id={`${id}-sort`}
          value={config.sort ? `${config.sort.field}::${config.sort.direction}` : ""}
          onChange={(e) => {
            const [field, direction] = e.target.value.split("::");
            set({ sort: field ? { field, direction: direction as "asc" | "desc" } : null });
          }}
        >
          <option value="">Result order</option>
          {cols.flatMap((c) => [
            <option key={`${c}-d`} value={`${c}::desc`}>
              {c} ↓
            </option>,
            <option key={`${c}-a`} value={`${c}::asc`}>
              {c} ↑
            </option>,
          ])}
        </NativeSelect>
      </div>
      {config.type === "bar" || config.type === "stacked_bar" ? (
        <label className="col-span-2 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!config.horizontal} onChange={(e) => set({ horizontal: e.target.checked })} />
          Horizontal bars
        </label>
      ) : null}
    </div>
  );
}

function KpiFromResult({ result, config }: { result: QueryResult; config: ChartConfig }) {
  const cols = result.columns;
  const vi = colIndex(cols, config.value ?? config.y?.[0] ?? cols.find((c) => toNumber(result.rows[0]?.[cols.indexOf(c)]) !== null)?.name);
  const bi = colIndex(cols, config.y?.[1] ?? config.compare ?? null);
  const row = result.rows[0] ?? [];
  return (
    <div className="flex h-full items-center px-4">
      <Kpi
        size="lg"
        label={config.title || cols[vi]?.name || "Value"}
        value={toNumber(row[vi])}
        baseline={bi >= 0 ? toNumber(row[bi]) : undefined}
        format={config.format}
        comparisonLabel={bi >= 0 ? `vs ${cols[bi].name}` : undefined}
      />
    </div>
  );
}

/**
 * A chart bound to a query result: suggestion-driven default type, type switcher, axis config,
 * PNG export (ECharts getDataURL), Inspector drawer and a filter-context chip row.
 */
export function ChartView({
  result,
  config: controlled,
  onConfigChange,
  provenance,
  title,
  height = 280,
  toolbar = true,
  className,
  showFilters = true,
  exportName,
}: {
  result: QueryResult;
  config?: ChartConfig | null;
  onConfigChange?: (c: ChartConfig) => void;
  provenance?: ChartProvenance;
  title?: string;
  height?: number | string;
  toolbar?: boolean;
  className?: string;
  showFilters?: boolean;
  exportName?: string;
}) {
  const suggestions = useChartSuggestions(result);
  const [local, setLocal] = React.useState<ChartConfig | null>(null);
  const config: ChartConfig = controlled ?? local ?? suggestions[0]?.config ?? { type: "table" };
  const setConfig = (c: ChartConfig) => (onConfigChange ? onConfigChange(c) : setLocal(c));
  const [inspect, setInspect] = React.useState(false);
  const chartRef = React.useRef<EChartHandle>(null);
  const themeVersion = useThemeVersion();

  const option = React.useMemo(
    () => (config.type === "kpi" || config.type === "table" ? null : buildChartOption(config, result, readPalette())),
    // themeVersion forces a rebuild with the new palette
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [config, result, themeVersion],
  );
  const reason = suggestions.find((s) => s.config.type === config.type)?.reason;
  const suggestedTypes = new Set(suggestions.filter((s) => s.score >= 50).map((s) => s.config.type));

  const exportPng = () => {
    const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() || "#ffffff";
    const url = chartRef.current?.toDataURL(bg);
    if (!url) {
      toast.error("This view has no chart image to export");
      return;
    }
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(exportName ?? title ?? "chart").replace(/[^\w.-]+/g, "_")}.png`;
    a.click();
  };

  return (
    <div className={cn("flex min-w-0 flex-col", className)} data-chart-type={config.type}>
      {toolbar ? (
        <div className="flex flex-wrap items-center gap-1 pb-1.5 no-print">
          <div className="flex flex-wrap items-center gap-0.5" role="radiogroup" aria-label="Chart type">
            {ALL_CHART_TYPES.map((t) => (
              <button
                key={t}
                type="button"
                role="radio"
                aria-checked={config.type === t}
                onClick={() => {
                  const s = suggestions.find((x) => x.config.type === t);
                  setConfig(s ? { ...s.config, format: config.format } : { ...config, type: t });
                }}
                className={cn(
                  "inline-flex h-6 items-center gap-1 rounded-sm px-1.5 text-xs text-fg-subtle hover:bg-bg-muted hover:text-fg",
                  config.type === t && "bg-accent-soft font-medium text-accent-soft-fg hover:bg-accent-soft",
                )}
                title={suggestions.find((x) => x.config.type === t)?.reason ?? CHART_TYPE_LABELS[t]}
              >
                {CHART_TYPE_LABELS[t]}
                {suggestedTypes.has(t) && config.type !== t && t !== "table" ? (
                  <>
                    <span className="size-1 rounded-full bg-accent/60" aria-hidden />
                    <span className="sr-only">(suggested)</span>
                  </>
                ) : null}
              </button>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-0.5">
            <Popover>
              <Tooltip content="Configure axes">
                <PopoverTrigger asChild>
                  <Button variant="ghost" size="icon-xs" aria-label="Configure chart">
                    <SlidersHorizontal />
                  </Button>
                </PopoverTrigger>
              </Tooltip>
              <PopoverContent align="end" className="w-80">
                <ChartConfigEditor config={config} onChange={setConfig} result={result} />
              </PopoverContent>
            </Popover>
            <Tooltip content="Download PNG">
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={exportPng}
                aria-label="Download chart as PNG"
                disabled={config.type === "table" || config.type === "kpi"}
              >
                <ImageDown />
              </Button>
            </Tooltip>
            <Button variant="ghost" size="xs" onClick={() => setInspect(true)} aria-label="Inspect chart">
              <ScanSearch /> Inspect
            </Button>
          </div>
        </div>
      ) : null}
      <div className="relative min-h-0" style={{ height }}>
        {config.type === "table" ? (
          <DataGrid columns={result.columns} rows={result.rows} className="h-full rounded border border-border" />
        ) : config.type === "kpi" ? (
          <KpiFromResult result={result} config={config} />
        ) : result.rows.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-fg-subtle">No rows to chart.</div>
        ) : option ? (
          <EChart ref={chartRef} option={option} ariaLabel={`${CHART_TYPE_LABELS[config.type]} chart${title ? `: ${title}` : ""}`} />
        ) : null}
      </div>
      {showFilters ? (
        <div className="flex items-center gap-2 pt-1.5">
          <FilterChips items={provenance?.filters ?? []} size="xs" />
          {result.truncated ? (
            <span className="text-2xs text-warning">Result truncated at {result.rows.length.toLocaleString()} rows</span>
          ) : null}
        </div>
      ) : null}
      <ChartInspector
        open={inspect}
        onOpenChange={setInspect}
        config={config}
        provenance={{ ...provenance, sql: provenance?.sql ?? result.sql ?? null }}
        reason={reason}
        title={title}
      />
    </div>
  );
}
