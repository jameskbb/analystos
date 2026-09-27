"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { GitBranch, Play } from "lucide-react";
import { analysis, type AnalysisOut, type AnomalyResult, type Sensitivity } from "@/lib/api/resources/analysis";
import { useWorkspace } from "@/components/providers/workspace";
import { EChart, useThemeVersion } from "@/components/charts/echart";
import { readPalette } from "@/lib/charts/option";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/label";
import { Segmented } from "@/components/ui/tabs";
import { Spinner } from "@/components/ui/spinner";
import { Panel } from "@/components/shell/page";
import { InlineError } from "@/components/states/states";
import { formatPctChange, formatValue } from "@/lib/format";
import { anomalyOption } from "./charts";
import { ResultMeta } from "./result-meta";
import { SeriesSourceForm, seriesSource, useMetricOptions, type SeriesDraft, useAnalysisRun } from "./sources";

/** Preset sensitivity or a custom z threshold (higher threshold = fewer, larger anomalies). */
export type SensitivityChoice = { preset: Sensitivity } | { z: number };

export function sensitivityParam(s: SensitivityChoice): Sensitivity | number {
  return "preset" in s ? s.preset : s.z;
}

export function AnomalyView({
  out,
  format,
  metricId,
  grain,
  filters,
}: {
  out: AnalysisOut<AnomalyResult>;
  format: string | null;
  metricId: string;
  grain: string;
  filters: { dimension: string; op: string; values: unknown[] }[];
}) {
  const { id: ws, href } = useWorkspace();
  const router = useRouter();
  const theme = useThemeVersion();
  const r = out.result;
  const option = React.useMemo(
    () => anomalyOption(r, format, readPalette()),
    // theme forces a rebuild with the new palette
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [r, format, theme],
  );
  const investigate = useMutation({
    mutationFn: (date: string) =>
      analysis.investigateAnomaly(ws, {
        metric_id: metricId,
        date,
        grain: grain === "day" ? "day" : "month",
        filters,
        ...(grain === "day" ? { baseline: "same_weekday_last_week" as const } : {}),
      }),
    onSuccess: (inv) => router.push(href(`/investigate/${inv.id}`)),
    onError: (e: Error) => toast.error("Could not start the investigation", { description: e.message }),
  });
  const canInvestigate = grain === "day" || grain === "month";
  return (
    <ResultMeta out={out}>
      <EChart option={option} ariaLabel="Metric with expected range and anomalies" className="h-64 w-full" />
      <p className="text-xs text-fg-subtle">
        Method {r.method}; threshold |z| ≥ {r.threshold} ({r.sensitivity}); window {r.window}
        {r.seasonal_period ? `; seasonal period ${r.seasonal_period}` : ""}. Points inside the grey band are normal variation.
      </p>
      <Panel title={`Anomalies (${r.anomalies.length})`}>
        {r.anomalies.length ? (
          <table className="w-full text-sm" aria-label="Anomalies">
            <thead className="text-left text-xs text-fg-subtle">
              <tr>
                <th className="px-3 py-1.5 font-medium">Period</th>
                <th className="px-3 py-1.5 text-right font-medium">Actual</th>
                <th className="px-3 py-1.5 text-right font-medium">Expected</th>
                <th className="px-3 py-1.5 text-right font-medium">Score (z)</th>
                <th className="px-3 py-1.5" />
              </tr>
            </thead>
            <tbody>
              {r.anomalies.map((a) => {
                const date = String(a.timestamp).slice(0, 10);
                return (
                  <tr key={date} className="border-t border-border">
                    <td className="px-3 py-1.5">
                      {date} <span className={a.direction === "up" ? "text-positive" : "text-negative"}>{a.direction === "up" ? "▲" : "▼"}</span>
                    </td>
                    <td className="px-3 py-1.5 text-right tabular">{formatValue(a.value, format)}</td>
                    <td className="px-3 py-1.5 text-right tabular text-fg-muted">{formatValue(a.expected, format)}</td>
                    <td className="px-3 py-1.5 text-right tabular">{a.score === null ? "n/a" : a.score.toFixed(1)}</td>
                    <td className="px-3 py-1.5 text-right">
                      {canInvestigate ? (
                        <Button
                          size="xs"
                          onClick={() => investigate.mutate(date)}
                          disabled={investigate.isPending}
                          aria-label={`Investigate ${date}`}
                        >
                          {investigate.isPending && investigate.variables === date ? <Spinner /> : <GitBranch />} Investigate
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <p className="px-3 py-2 text-sm text-fg-muted">No point is outside the expected range at this sensitivity.</p>
        )}
      </Panel>
      {r.change_points.length ? (
        <Panel title={`Level shifts (${r.change_points.length})`}>
          <table className="w-full text-sm" aria-label="Level shifts">
            <thead className="text-left text-xs text-fg-subtle">
              <tr>
                <th className="px-3 py-1.5 font-medium">From</th>
                <th className="px-3 py-1.5 text-right font-medium">Mean before</th>
                <th className="px-3 py-1.5 text-right font-medium">Mean after</th>
                <th className="px-3 py-1.5 text-right font-medium">Change</th>
              </tr>
            </thead>
            <tbody>
              {r.change_points.map((c) => (
                <tr key={c.index} className="border-t border-border">
                  <td className="px-3 py-1.5">{String(c.timestamp).slice(0, 10)}</td>
                  <td className="px-3 py-1.5 text-right tabular">{formatValue(c.mean_before, format)}</td>
                  <td className="px-3 py-1.5 text-right tabular">{formatValue(c.mean_after, format)}</td>
                  <td className="px-3 py-1.5 text-right tabular">{formatPctChange(c.pct_change)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ) : null}
    </ResultMeta>
  );
}

export function AnomalyPanel({ draft, onDraft }: { draft: SeriesDraft; onDraft: (d: SeriesDraft) => void }) {
  const { id: ws } = useWorkspace();
  const { metrics } = useMetricOptions();
  const [sens, setSens] = React.useState<SensitivityChoice>({ preset: "medium" });
  const source = seriesSource(draft);
  const run = useAnalysisRun(() => analysis.anomalies(ws, { ...source, sensitivity: sensitivityParam(sens), change_points: true }));
  const format = metrics.find((m) => m.id === draft.metricId)?.format ?? null;
  const z = "z" in sens ? sens.z : sens.preset === "low" ? 4 : sens.preset === "high" ? 2.5 : 3;
  return (
    <div className="flex flex-col gap-4">
      <Panel title="Detect anomalies" description="Robust deviations from a seasonal baseline, plus level shifts. Not every wiggle is an anomaly.">
        <form
          className="flex flex-col gap-3 p-3"
          onSubmit={(e) => {
            e.preventDefault();
            run.mutate();
          }}
        >
          <SeriesSourceForm value={draft} onChange={onDraft} />
          <div className="flex flex-wrap items-end gap-4">
            <Field label="Sensitivity">
              <Segmented<Sensitivity | "custom">
                aria-label="Sensitivity"
                value={"preset" in sens ? sens.preset : "custom"}
                onChange={(v) => setSens(v === "custom" ? { z } : { preset: v })}
                options={[
                  { value: "low", label: "Low" },
                  { value: "medium", label: "Medium" },
                  { value: "high", label: "High" },
                  { value: "custom", label: "Custom" },
                ]}
              />
            </Field>
            <Field label={`Threshold |z| ≥ ${z.toFixed(1)}`} htmlFor="an-z">
              <input
                id="an-z"
                type="range"
                min={1.5}
                max={8}
                step={0.1}
                value={z}
                onChange={(e) => setSens({ z: Number(e.target.value) })}
                className="w-56 accent-[var(--accent)]"
              />
            </Field>
          </div>
          <div>
            <Button type="submit" variant="primary" disabled={!draft.metricId || run.isPending}>
              {run.isPending ? <Spinner /> : <Play />} Detect
            </Button>
          </div>
          {run.error ? <InlineError error={run.error} /> : null}
        </form>
      </Panel>
      {run.data ? (
        <Panel title="Result" bodyClassName="p-3">
          <AnomalyView
            key={run.data.id}
            out={run.data}
            format={format}
            metricId={draft.metricId}
            grain={draft.grain}
            filters={source.filters ?? []}
          />
        </Panel>
      ) : null}
    </div>
  );
}
