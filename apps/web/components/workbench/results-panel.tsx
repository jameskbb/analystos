"use client";
import * as React from "react";
import { AlertTriangle, BarChart3, Clock, Rows3, ShieldAlert, Table as TableIcon, TimerOff } from "lucide-react";
import type { QueryRunResultOut } from "@/lib/api/resources/workbench";
import type { ChartConfig, QueryResult } from "@/lib/api/types";
import { ApiError } from "@/lib/api/client";
import { DataGrid } from "@/components/data-grid/data-grid";
import { ChartView } from "@/components/charts/chart-view";
import { ExportMenu } from "@/components/export/export-menu";
import { Segmented } from "@/components/ui/tabs";
import { Spinner } from "@/components/ui/spinner";
import { formatDuration, formatInt } from "@/lib/format";
import { useElementHeight } from "./use-media";
import { cn } from "@/lib/utils";

/** Classifies a query failure so rejections (read-only enforcement) read differently from SQL errors. */
export function describeQueryError(error: unknown): { title: string; message: string; kind: "rejected" | "timeout" | "failed" } {
  const message = error instanceof Error ? error.message : String(error);
  if (error instanceof ApiError) {
    if (error.code === "unsafe_sql")
      return {
        kind: "rejected",
        title: "Rejected by read-only enforcement",
        message: `${message}\nOnly single SELECT / WITH / VALUES statements run. Nothing was executed.`,
      };
    if (error.code === "query_timeout" || error.status === 408)
      return { kind: "timeout", title: "Query timed out", message };
  }
  return { kind: "failed", title: "Query failed", message };
}

export function QueryErrorView({ error, className }: { error: unknown; className?: string }) {
  const d = describeQueryError(error);
  const Icon = d.kind === "rejected" ? ShieldAlert : d.kind === "timeout" ? TimerOff : AlertTriangle;
  return (
    <div role="alert" className={cn("m-2 rounded border border-negative/30 bg-negative-soft px-3 py-2", className)}>
      <div className="flex items-center gap-1.5 text-sm font-medium text-negative">
        <Icon className="size-3.5" /> {d.title}
      </div>
      <pre className="mt-1 font-mono text-xs whitespace-pre-wrap text-fg">{d.message}</pre>
    </div>
  );
}

/** Result area for a SQL run: grid or chart, run stats, warnings and export. */
export function ResultsPanel({
  run,
  error,
  running,
  chartConfig,
  onChartConfigChange,
  emptyHint,
}: {
  run: QueryRunResultOut | null;
  error: unknown;
  running: boolean;
  chartConfig?: ChartConfig | null;
  onChartConfigChange?: (c: ChartConfig) => void;
  emptyHint?: React.ReactNode;
}) {
  const [view, setView] = React.useState<"table" | "chart">("table");
  const [bodyRef, bodyHeight] = useElementHeight<HTMLDivElement>();
  const result: QueryResult | null = run?.result ?? null;

  return (
    <div className="flex h-full min-h-0 flex-col" aria-busy={running}>
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border px-2">
        <Segmented
          aria-label="Result view"
          value={view}
          onChange={setView}
          options={[
            { value: "table", label: (<><TableIcon /> Results</>) },
            { value: "chart", label: (<><BarChart3 /> Chart</>) },
          ]}
        />
        {running ? (
          <span className="flex items-center gap-1.5 text-xs text-fg-subtle">
            <Spinner /> Running…
          </span>
        ) : run ? (
          <div className="flex min-w-0 items-center gap-3 text-xs text-fg-subtle">
            <span className="flex items-center gap-1 tabular">
              <Rows3 className="size-3" /> {formatInt(run.row_count)} {run.row_count === 1 ? "row" : "rows"}
            </span>
            <span className="flex items-center gap-1 tabular">
              <Clock className="size-3" /> {formatDuration(run.elapsed_ms)}
            </span>
            {run.truncated ? (
              <span className="text-warning">Truncated at {formatInt(result?.rows.length ?? 0)} rows</span>
            ) : null}
          </div>
        ) : null}
        <div className="ml-auto">
          {run && !running ? (
            <ExportMenu target={{ kind: "query_run", id: run.id }} formats={["csv", "xlsx", "json"]} filename={`query-${run.id.slice(0, 8)}`} size="xs" />
          ) : null}
        </div>
      </div>
      {run?.warnings?.length ? (
        <ul className="shrink-0 border-b border-border bg-warning-soft px-3 py-1 text-xs text-warning">
          {run.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      ) : null}
      <div ref={bodyRef} className="relative min-h-0 flex-1">
        {error && !running ? (
          <QueryErrorView error={error} />
        ) : !result ? (
          <div className="flex h-full items-center justify-center px-4 text-center text-sm text-fg-subtle">
            {running ? "Running query…" : (emptyHint ?? "Run a query to see results.")}
          </div>
        ) : view === "table" ? (
          <DataGrid columns={result.columns} rows={result.rows} className="absolute inset-0" />
        ) : (
          <div className="absolute inset-0 overflow-auto p-2">
            <ChartView
              result={result}
              config={chartConfig ?? undefined}
              onConfigChange={onChartConfigChange}
              height={Math.max(bodyHeight - 80, 180)}
              provenance={{
                sql: run?.sql ?? result.sql ?? null,
                datasets: Object.keys(run?.dataset_versions ?? {}),
                query_run_id: run?.id ?? null,
                filters: [],
              }}
              exportName={`query-${run?.id.slice(0, 8) ?? "result"}`}
            />
          </div>
        )}
      </div>
    </div>
  );
}
