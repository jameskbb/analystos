"use client";
import * as React from "react";
import { Play } from "lucide-react";
import { analysis, type AnalysisOut, type ForecastModelName, type ForecastResult } from "@/lib/api/resources/analysis";
import { useWorkspace } from "@/components/providers/workspace";
import { EChart, useThemeVersion } from "@/components/charts/echart";
import { readPalette } from "@/lib/charts/option";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Field } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import { Panel } from "@/components/shell/page";
import { InlineError } from "@/components/states/states";
import { formatValue } from "@/lib/format";
import { cn } from "@/lib/utils";
import { backtestRows, forecastOption, MODEL_LABEL, pickModel } from "./charts";
import { ResultMeta } from "./result-meta";
import { SeriesSourceForm, seriesSource, useMetricOptions, type SeriesDraft, useAnalysisRun } from "./sources";

const ALL_MODELS: ForecastModelName[] = ["naive", "seasonal_naive", "ets", "arima"];

export function ForecastView({ out, format }: { out: AnalysisOut<ForecastResult>; format: string | null }) {
  const r = out.result;
  const [model, setModel] = React.useState<ForecastModelName | null>(r.best_model);
  const theme = useThemeVersion();
  const option = React.useMemo(
    () => forecastOption(r, model, format, readPalette()),
    // theme forces a rebuild with the new palette
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [r, model, format, theme],
  );
  const chosen = pickModel(r, model);
  const rows = backtestRows(r);
  return (
    <ResultMeta out={out}>
      <EChart option={option} ariaLabel="Forecast with prediction interval" className="h-72 w-full" />
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Panel title="Backtest accuracy" description={`Rolling-origin backtest; ranked by ${r.selection_metric}`}>
          <table className="w-full text-sm" aria-label="Backtest accuracy">
            <thead className="text-left text-xs text-fg-subtle">
              <tr>
                <th className="px-3 py-1.5 font-medium">Model</th>
                <th className="px-3 py-1.5 text-right font-medium">MAE</th>
                <th className="px-3 py-1.5 text-right font-medium">MAPE</th>
                <th className="px-3 py-1.5 text-right font-medium" title="Share of held-out actuals inside the interval">
                  Interval coverage
                </th>
                <th className="px-3 py-1.5 text-right font-medium">Folds</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((m) => (
                <tr
                  key={m.name}
                  className={cn(
                    "cursor-pointer border-t border-border hover:bg-bg-subtle",
                    chosen?.name === m.name && "bg-accent-soft/50",
                  )}
                  onClick={() => !m.error && setModel(m.name)}
                  aria-selected={chosen?.name === m.name}
                >
                  <td className="px-3 py-1.5">
                    <span className="flex items-center gap-1.5">
                      {m.label}
                      {m.best ? <Badge tone="positive">best</Badge> : null}
                    </span>
                    {m.error ? <span className="block text-xs text-negative">Not fitted: {m.error}</span> : null}
                    {!m.error && m.note ? <span className="block text-xs text-fg-subtle">{m.note}</span> : null}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular">{m.mae === null ? "n/a" : formatValue(m.mae, format)}</td>
                  <td className="px-3 py-1.5 text-right tabular">{m.mape === null ? "n/a" : formatValue(m.mape, "percent")}</td>
                  <td className="px-3 py-1.5 text-right tabular">
                    {m.coverage === null ? "n/a" : formatValue(m.coverage, "percent", { digits: 0 })}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular">{m.folds}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.every((m) => m.mae === null) ? (
            <p role="note" className="border-t border-border px-3 py-1.5 text-xs text-warning">
              No model could be backtested on {r.history.length} points at this grain. Use a finer grain or a longer window
              before choosing a model; the intervals alone do not show accuracy.
            </p>
          ) : null}
          <p className="border-t border-border px-3 py-1.5 text-xs text-fg-subtle">
            Click a model to chart it. Nominal interval {formatValue(r.interval, "percent", { digits: 0 })}; coverage well
            below that means the band is too narrow.
          </p>
        </Panel>
        <Panel title="Forecast" description={chosen ? MODEL_LABEL[chosen.name] : undefined}>
          <table className="w-full text-sm" aria-label="Forecast values">
            <thead className="text-left text-xs text-fg-subtle">
              <tr>
                <th className="px-3 py-1.5 font-medium">Period</th>
                <th className="px-3 py-1.5 text-right font-medium">Low</th>
                <th className="px-3 py-1.5 text-right font-medium">Forecast</th>
                <th className="px-3 py-1.5 text-right font-medium">High</th>
              </tr>
            </thead>
            <tbody>
              {(chosen?.points ?? []).map((p) => (
                <tr key={p.timestamp} className="border-t border-border">
                  <td className="px-3 py-1.5">{String(p.timestamp).slice(0, 10)}</td>
                  <td className="px-3 py-1.5 text-right tabular text-fg-muted">{formatValue(p.lower, format, { compact: true })}</td>
                  <td className="px-3 py-1.5 text-right tabular">{formatValue(p.mean, format, { compact: true })}</td>
                  <td className="px-3 py-1.5 text-right tabular text-fg-muted">{formatValue(p.upper, format, { compact: true })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>
    </ResultMeta>
  );
}

export function ForecastPanel({ draft, onDraft }: { draft: SeriesDraft; onDraft: (d: SeriesDraft) => void }) {
  const { id: ws } = useWorkspace();
  const { metrics } = useMetricOptions();
  const [horizon, setHorizon] = React.useState(6);
  const [interval, setIntervalLevel] = React.useState(0.8);
  const [models, setModels] = React.useState<Set<ForecastModelName>>(new Set(ALL_MODELS));
  const [folds, setFolds] = React.useState(3);
  const [season, setSeason] = React.useState("");
  const run = useAnalysisRun(() =>
    analysis.forecast(ws, {
      ...seriesSource(draft),
      horizon,
      interval,
      models: [...models],
      backtest_folds: folds,
      ...(season.trim() ? { seasonal_period: Number(season) } : {}),
    }),
  );
  const format = metrics.find((m) => m.id === draft.metricId)?.format ?? null;
  return (
    <div className="flex flex-col gap-4">
      <Panel title="Forecast a metric" description="Baseline models compared on held-out history; no single-number false precision.">
        <form
          className="flex flex-col gap-3 p-3"
          onSubmit={(e) => {
            e.preventDefault();
            run.mutate();
          }}
        >
          <SeriesSourceForm value={draft} onChange={onDraft} />
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Field label="Horizon (periods)" htmlFor="fc-h">
              <Input id="fc-h" type="number" min={1} max={120} value={horizon} onChange={(e) => setHorizon(Number(e.target.value) || 1)} />
            </Field>
            <Field label="Interval" htmlFor="fc-i">
              <Input
                id="fc-i"
                type="number"
                min={0.5}
                max={0.99}
                step={0.05}
                value={interval}
                onChange={(e) => setIntervalLevel(Number(e.target.value) || 0.8)}
              />
            </Field>
            <Field label="Backtest folds" htmlFor="fc-f">
              <Input id="fc-f" type="number" min={1} max={10} value={folds} onChange={(e) => setFolds(Number(e.target.value) || 1)} />
            </Field>
            <Field
              label="Seasonal period"
              htmlFor="fc-s"
              hint="Blank = detect (7 daily, 52 weekly, 12 monthly). Seasonal models need two full seasons of history."
            >
              <Input id="fc-s" type="number" min={1} max={366} placeholder="auto" value={season} onChange={(e) => setSeason(e.target.value)} />
            </Field>
            <fieldset className="flex flex-col gap-1">
              <legend className="mb-1 text-xs font-medium text-fg-muted">Models</legend>
              {ALL_MODELS.map((m) => (
                <label key={m} className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={models.has(m)}
                    onCheckedChange={(c) =>
                      setModels((prev) => {
                        const next = new Set(prev);
                        if (c) next.add(m);
                        else next.delete(m);
                        return next;
                      })
                    }
                    aria-label={MODEL_LABEL[m]}
                  />
                  {MODEL_LABEL[m]}
                </label>
              ))}
            </fieldset>
          </div>
          <div>
            <Button type="submit" variant="primary" disabled={!draft.metricId || !models.size || run.isPending}>
              {run.isPending ? <Spinner /> : <Play />} Run forecast
            </Button>
          </div>
          {run.error ? <InlineError error={run.error} /> : null}
        </form>
      </Panel>
      {run.data ? (
        <Panel title="Result" bodyClassName="p-3">
          <ForecastView key={run.data.id} out={run.data} format={format} />
        </Panel>
      ) : null}
    </div>
  );
}
