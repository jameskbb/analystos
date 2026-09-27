"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { dimensions as dimensionsApi, metrics as metricsApi, queries } from "@/lib/api/endpoints";
import type { Grain, TabularSource, TimeSeriesSource } from "@/lib/api/resources/analysis";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Field } from "@/components/ui/label";
import { Input, NativeSelect } from "@/components/ui/input";
import { Segmented } from "@/components/ui/tabs";
import { CodeEditor } from "@/components/code/code-editor";
import { exclusiveEnd } from "@/components/workbench/explore/build";

export interface SeriesDraft {
  metricId: string;
  start: string;
  /** Inclusive end date as the user picks it; sent as the half-open end (+1 day). */
  end: string;
  grain: Grain;
  filterDimension: string;
  filterValues: string;
}

export function seriesSource(d: SeriesDraft): TimeSeriesSource {
  const values = d.filterValues
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
  return {
    metric_id: d.metricId,
    start: d.start,
    end: exclusiveEnd(d.end),
    grain: d.grain,
    filters: d.filterDimension && values.length ? [{ dimension: d.filterDimension, op: "in", values }] : [],
  };
}

export function useMetricOptions() {
  const { id: ws } = useWorkspace();
  const q = useQuery({ queryKey: qk.metrics(ws), queryFn: () => metricsApi.list(ws) });
  return { metrics: (q.data ?? []).filter((m) => !m.archived), isLoading: q.isLoading };
}

export function SeriesSourceForm({ value, onChange }: { value: SeriesDraft; onChange: (v: SeriesDraft) => void }) {
  const { id: ws } = useWorkspace();
  const { metrics } = useMetricOptions();
  const dims = useQuery({ queryKey: qk.dimensions(ws), queryFn: () => dimensionsApi.list(ws) });
  const set = (p: Partial<SeriesDraft>) => onChange({ ...value, ...p });
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Field label="Metric" htmlFor="an-metric">
        <NativeSelect id="an-metric" value={value.metricId} onChange={(e) => set({ metricId: e.target.value })}>
          <option value="">Choose a metric…</option>
          {metrics.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label || m.name}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field label="From" htmlFor="an-start">
        <Input id="an-start" type="date" value={value.start} onChange={(e) => set({ start: e.target.value })} />
      </Field>
      <Field label="To (inclusive)" htmlFor="an-end">
        <Input id="an-end" type="date" value={value.end} onChange={(e) => set({ end: e.target.value })} />
      </Field>
      <Field label="Grain">
        <Segmented<Grain>
          aria-label="Grain"
          value={value.grain}
          onChange={(g) => set({ grain: g })}
          options={[
            { value: "day", label: "Day" },
            { value: "week", label: "Week" },
            { value: "month", label: "Month" },
            { value: "quarter", label: "Quarter" },
          ]}
        />
      </Field>
      <Field label="Only where (optional)" htmlFor="an-fdim">
        <NativeSelect id="an-fdim" value={value.filterDimension} onChange={(e) => set({ filterDimension: e.target.value })}>
          <option value="">All data</option>
          {(dims.data ?? [])
            .filter((d) => d.type !== "time")
            .map((d) => (
              <option key={d.name} value={d.name}>
                {d.label || d.name}
              </option>
            ))}
        </NativeSelect>
      </Field>
      <Field label="is one of" htmlFor="an-fval" hint="Comma-separated values">
        <Input
          id="an-fval"
          value={value.filterValues}
          disabled={!value.filterDimension}
          placeholder="e.g. Dallas, Houston"
          onChange={(e) => set({ filterValues: e.target.value })}
        />
      </Field>
    </div>
  );
}

export interface TabularDraft {
  mode: "table" | "sql";
  table: string;
  sql: string;
}

export function tabularSource(d: TabularDraft): TabularSource {
  return d.mode === "sql" ? { sql: d.sql } : { table: d.table };
}

/** Columns available for the chosen source (known up front for tables; typed by the user for SQL). */
export function useTabularColumns(d: TabularDraft): { name: string; type: string }[] {
  const { id: ws } = useWorkspace();
  const schema = useQuery({ queryKey: qk.schema(ws), queryFn: () => queries.schema(ws) });
  if (d.mode !== "table") return [];
  return schema.data?.tables.find((t) => t.table === d.table)?.columns ?? [];
}

export function TabularSourceForm({ value, onChange }: { value: TabularDraft; onChange: (v: TabularDraft) => void }) {
  const { id: ws } = useWorkspace();
  const schema = useQuery({ queryKey: qk.schema(ws), queryFn: () => queries.schema(ws) });
  const set = (p: Partial<TabularDraft>) => onChange({ ...value, ...p });
  return (
    <div className="flex flex-col gap-2">
      <Segmented<"table" | "sql">
        aria-label="Input"
        value={value.mode}
        onChange={(m) => set({ mode: m })}
        options={[
          { value: "table", label: "Table" },
          { value: "sql", label: "SQL query" },
        ]}
      />
      {value.mode === "table" ? (
        <Field label="Table" htmlFor="an-table">
          <NativeSelect id="an-table" value={value.table} onChange={(e) => set({ table: e.target.value })}>
            <option value="">Choose a table…</option>
            {(schema.data?.tables ?? []).map((t) => (
              <option key={t.table} value={t.table}>
                {t.dataset_name ? `${t.dataset_name} (${t.table})` : t.table}
              </option>
            ))}
          </NativeSelect>
        </Field>
      ) : (
        <Field label="Read-only SQL" hint="Its result columns are the inputs. The same safety rules as the SQL workspace apply.">
          <div className="rounded border border-border-strong">
            <CodeEditor value={value.sql} onChange={(sql) => set({ sql })} language="sql" minHeight={110} ariaLabel="Analysis input SQL" />
          </div>
        </Field>
      )}
    </div>
  );
}

/** A column picker that falls back to free text when the columns are not known (SQL input). */
export function ColumnInput({
  id,
  value,
  onChange,
  columns,
  numericOnly,
  placeholder,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  columns: { name: string; type: string }[];
  numericOnly?: boolean;
  placeholder?: string;
}) {
  const opts = numericOnly ? columns.filter((c) => /int|dec|num|float|double|real|hugeint/i.test(c.type)) : columns;
  if (!columns.length)
    return <Input id={id} value={value} placeholder={placeholder ?? "column name"} onChange={(e) => onChange(e.target.value)} />;
  return (
    <NativeSelect id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Choose…</option>
      {opts.map((c) => (
        <option key={c.name} value={c.name}>
          {c.name}
        </option>
      ))}
    </NativeSelect>
  );
}

/** Runs an analysis and refreshes the "Recent analyses" list (each run is a saved artifact). */
export function useAnalysisRun<R>(fn: () => Promise<R>) {
  const { id: ws } = useWorkspace();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ws", ws, "analysis"] }),
  });
}
