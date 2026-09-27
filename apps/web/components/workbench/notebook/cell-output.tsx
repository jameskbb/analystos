"use client";
import * as React from "react";
import type { CellOutput, QueryResult } from "@/lib/api/types";
import { DataGrid } from "@/components/data-grid/data-grid";
import { formatDuration, formatInt } from "@/lib/format";
import { cn } from "@/lib/utils";

function ResultGrid({ result, label }: { result: QueryResult; label: string }) {
  const height = Math.min(36 + Math.max(result.rows.length, 1) * 26, 320);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2 text-2xs text-fg-subtle tabular">
        <span className="font-medium text-fg-muted">{label}</span>
        <span>
          {formatInt(result.row_count)} rows · {result.columns.length} columns
        </span>
        {result.elapsed_ms ? <span>· {formatDuration(result.elapsed_ms)}</span> : null}
        {result.truncated ? <span className="text-warning">· truncated at {formatInt(result.rows.length)}</span> : null}
      </div>
      <DataGrid columns={result.columns} rows={result.rows} height={height} className="rounded border border-border" ariaLabel={label} />
    </div>
  );
}

/** Output area for SQL and Python cells: errors, stdout/stderr, tables and figures. */
export function CellOutputView({ output, className }: { output: CellOutput | null | undefined; className?: string }) {
  if (!output) return null;
  const frames = Object.entries(output.dataframes ?? {});
  const empty =
    !output.error && !output.result && !output.stdout && !output.stderr && !frames.length && !(output.figures ?? []).length;
  if (empty) return <div className={cn("text-xs text-fg-subtle italic", className)}>Ran successfully with no output.</div>;
  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {output.error ? (
        <pre role="alert" className="overflow-auto rounded border border-negative/30 bg-negative-soft px-2 py-1.5 font-mono text-xs whitespace-pre-wrap text-negative">
          {output.error}
        </pre>
      ) : null}
      {output.stdout ? (
        <pre className="max-h-72 overflow-auto rounded border border-border bg-code-bg px-2 py-1.5 font-mono text-xs whitespace-pre-wrap scrollbar-thin" aria-label="Standard output">
          {output.stdout}
        </pre>
      ) : null}
      {output.stderr ? (
        <pre className="max-h-48 overflow-auto rounded border border-warning/30 bg-warning-soft px-2 py-1.5 font-mono text-xs whitespace-pre-wrap text-warning scrollbar-thin" aria-label="Standard error">
          {output.stderr}
        </pre>
      ) : null}
      {output.result ? <ResultGrid result={output.result} label="Result" /> : null}
      {frames.map(([name, df]) => (
        <ResultGrid key={name} result={df} label={name} />
      ))}
      {(output.figures ?? []).map((png, i) => (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          key={i}
          src={png.startsWith("data:") ? png : `data:image/png;base64,${png}`}
          alt={`Figure ${i + 1} produced by this cell`}
          className="max-w-full self-start rounded border border-border bg-white"
        />
      ))}
    </div>
  );
}

/** The tabular data a chart cell can plot from a source cell's output. */
export function outputTable(output: CellOutput | null | undefined, dataframe?: string | null): QueryResult | null {
  if (!output) return null;
  if (output.result) return output.result;
  const frames = output.dataframes ?? {};
  if (dataframe && frames[dataframe]) return frames[dataframe];
  const first = Object.values(frames)[0];
  return first ?? null;
}
