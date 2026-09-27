"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { artifacts as artifactsApi, metrics as metricsApi, queries as queriesApi } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import type { ChartType, Metric, TileKind } from "@/lib/api/types";
import type { Tile, TileInput } from "@/lib/api/resources/outputs";
import { useWorkspace } from "@/components/providers/workspace";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Segmented } from "@/components/ui/tabs";
import { InlineError } from "@/components/states/states";
import { ALL_CHART_TYPES, CHART_TYPE_LABELS } from "@/lib/charts/suggest";
import { humanize } from "@/lib/format";
import { useDimensions } from "./dashboard-controls";

type Source = "metric" | "saved_query" | "artifact";
const GRAINS = ["day", "week", "month", "quarter", "year"];

interface FormState {
  kind: TileKind;
  title: string;
  source: Source;
  metrics: string[];
  dimension: string;
  grain: string;
  compare: "pop" | "yoy" | "none";
  limit: string;
  savedQueryId: string;
  artifactId: string;
  chartType: ChartType | "";
  text: string;
}

function initialState(tile?: Tile | null): FormState {
  const mq = tile?.binding.metric_query;
  const dim = mq?.dimensions?.[0] ?? "";
  const [dimName, grain] = dim.includes("__") ? dim.split("__") : [dim, "month"];
  return {
    kind: tile?.kind ?? "kpi",
    title: tile?.title ?? "",
    source: tile?.binding.saved_query_id ? "saved_query" : tile?.binding.artifact_id ? "artifact" : "metric",
    metrics: mq?.metrics ?? [],
    dimension: dimName,
    grain: grain || "month",
    compare: tile?.binding.compare ?? "pop",
    limit: mq?.limit ? String(mq.limit) : "",
    savedQueryId: tile?.binding.saved_query_id ?? "",
    artifactId: tile?.binding.artifact_id ?? "",
    chartType: tile?.viz.chart?.type ?? "",
    text: tile?.text ?? "",
  };
}

function metricLabel(m: Metric) {
  return m.label || humanize(m.name || m.id);
}

/**
 * Add / edit a dashboard tile. Metric tiles are bound to semantic metrics, so every tile using
 * "Revenue" computes it the same way (spec §36); only presentation is configured per tile.
 */
export function TileDialog({
  open,
  onOpenChange,
  tile,
  onSubmit,
  pending,
  error,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  tile?: Tile | null;
  onSubmit: (input: TileInput) => void;
  pending?: boolean;
  error?: unknown;
}) {
  const { id: ws } = useWorkspace();
  const [s, setS] = React.useState<FormState>(() => initialState(tile));
  React.useEffect(() => {
    if (open) setS(initialState(tile));
  }, [open, tile]);
  const set = (patch: Partial<FormState>) => setS((prev) => ({ ...prev, ...patch }));

  const metrics = useQuery({ queryKey: qk.metrics(ws), queryFn: () => metricsApi.list(ws), enabled: open });
  const dims = useDimensions();
  const saved = useQuery({
    queryKey: qk.savedQueries(ws),
    queryFn: () => queriesApi.saved(ws),
    enabled: open && s.source === "saved_query",
  });
  const arts = useQuery({
    queryKey: ["ws", ws, "artifacts", "for-tiles"],
    queryFn: () => artifactsApi.list(ws, { limit: 100 }),
    enabled: open && s.source === "artifact",
  });

  const selectedDim = (dims.data ?? []).find((d) => d.name === s.dimension);
  const isTime = selectedDim?.type === "time";
  const needsData = s.kind !== "text";
  const metricMode = s.source === "metric";
  const effectiveSource: Source = s.kind === "kpi" ? "metric" : s.source;

  const metricList = metrics.data ?? [];
  const defaultTitle = (() => {
    if (s.kind === "text") return "Notes";
    if (effectiveSource === "metric") {
      const names = s.metrics.map((id) => {
        const m = metricList.find((x) => x.id === id);
        return m ? metricLabel(m) : id;
      });
      const by = s.dimension ? ` by ${selectedDim?.label || humanize(s.dimension)}` : "";
      return names.join(", ") + by;
    }
    if (effectiveSource === "saved_query") return saved.data?.find((q) => q.id === s.savedQueryId)?.name ?? "";
    return arts.data?.find((a) => a.id === s.artifactId)?.title ?? "";
  })();

  const valid =
    s.kind === "text"
      ? s.text.trim().length > 0
      : effectiveSource === "metric"
        ? s.metrics.length > 0 && (s.kind !== "kpi" || s.metrics.length === 1)
        : effectiveSource === "saved_query"
          ? !!s.savedQueryId
          : !!s.artifactId;

  const submit = () => {
    const title = s.title.trim() || defaultTitle || "Untitled tile";
    if (s.kind === "text") {
      onSubmit({ kind: "text", title, binding: {}, viz: {}, text: s.text });
      return;
    }
    const chart = s.chartType ? { type: s.chartType as ChartType } : null;
    const format = s.metrics.length === 1 ? (metricList.find((m) => m.id === s.metrics[0])?.format ?? null) : null;
    if (effectiveSource === "metric") {
      const dimension = s.dimension ? (isTime ? `${s.dimension}__${s.grain}` : s.dimension) : null;
      onSubmit({
        kind: s.kind,
        title,
        binding: {
          metric_query: {
            metrics: s.kind === "kpi" ? s.metrics.slice(0, 1) : s.metrics,
            dimensions: s.kind === "kpi" || !dimension ? [] : [dimension],
            filters: [],
            order_by:
              s.kind !== "kpi" && dimension && !isTime && s.metrics[0]
                ? [{ field: s.metrics[0], direction: "desc" }]
                : dimension && isTime
                  ? [{ field: dimension, direction: "asc" }]
                  : [],
            limit: s.limit ? Number(s.limit) : null,
          },
          compare: s.kind === "kpi" ? s.compare : null,
        },
        viz: { chart, format },
      });
    } else if (effectiveSource === "saved_query") {
      onSubmit({ kind: s.kind, title, binding: { saved_query_id: s.savedQueryId, params: {} }, viz: { chart } });
    } else {
      onSubmit({ kind: s.kind, title, binding: { artifact_id: s.artifactId }, viz: { chart } });
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <form
          className="flex min-h-0 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            if (valid) submit();
          }}
        >
          <DialogHeader>
            <DialogTitle>{tile ? "Edit tile" : "Add tile"}</DialogTitle>
            <DialogDescription>
              Metric tiles use the semantic layer definition and inherit the dashboard&apos;s filters and date range.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-3">
            <Segmented<TileKind>
              aria-label="Tile type"
              value={s.kind}
              onChange={(kind) => set({ kind, source: kind === "kpi" ? "metric" : s.source })}
              options={[
                { value: "kpi", label: "KPI" },
                { value: "chart", label: "Chart" },
                { value: "table", label: "Table" },
                { value: "text", label: "Text" },
              ]}
            />
            {needsData && s.kind !== "kpi" ? (
              <Segmented<Source>
                aria-label="Data source"
                value={s.source}
                onChange={(source) => set({ source })}
                options={[
                  { value: "metric", label: "Semantic metric" },
                  { value: "saved_query", label: "Saved query" },
                  { value: "artifact", label: "Investigation artifact" },
                ]}
              />
            ) : null}

            {s.kind === "text" ? (
              <Field label="Text (Markdown)" htmlFor="tile-text">
                <Textarea id="tile-text" rows={8} value={s.text} onChange={(e) => set({ text: e.target.value })} />
              </Field>
            ) : effectiveSource === "metric" ? (
              <div className="grid gap-3 sm:grid-cols-2">
                <Field
                  label={s.kind === "kpi" ? "Metric" : "Metrics"}
                  htmlFor="tile-metrics"
                  hint={s.kind === "kpi" ? "One metric per KPI." : "Hold Ctrl/⌘ to select several."}
                  className="sm:col-span-2"
                >
                  {metrics.isError ? (
                    <InlineError error={metrics.error} />
                  ) : (
                    <select
                      id="tile-metrics"
                      multiple={s.kind !== "kpi"}
                      size={s.kind === "kpi" ? undefined : 6}
                      className="w-full rounded border border-border-strong bg-bg px-1 py-1 text-sm"
                      value={s.kind === "kpi" ? (s.metrics[0] ?? "") : s.metrics}
                      onChange={(e) =>
                        set({
                          metrics:
                            s.kind === "kpi"
                              ? e.target.value
                                ? [e.target.value]
                                : []
                              : Array.from(e.target.selectedOptions).map((o) => o.value),
                        })
                      }
                    >
                      {s.kind === "kpi" ? <option value="">{metrics.isLoading ? "Loading metrics…" : "Choose a metric…"}</option> : null}
                      {metricList.map((m) => (
                        <option key={m.id} value={m.id}>
                          {metricLabel(m)}
                          {m.format ? ` · ${m.format}` : ""}
                        </option>
                      ))}
                    </select>
                  )}
                </Field>
                {s.kind === "kpi" ? (
                  <Field label="Comparison" htmlFor="tile-compare">
                    <NativeSelect id="tile-compare" value={s.compare} onChange={(e) => set({ compare: e.target.value as FormState["compare"] })}>
                      <option value="pop">Prior period</option>
                      <option value="yoy">Same period last year</option>
                      <option value="none">None</option>
                    </NativeSelect>
                  </Field>
                ) : (
                  <>
                    <Field label="Break down by" htmlFor="tile-dim">
                      <NativeSelect id="tile-dim" value={s.dimension} onChange={(e) => set({ dimension: e.target.value })}>
                        <option value="">No breakdown (total)</option>
                        {(dims.data ?? []).map((d) => (
                          <option key={d.name} value={d.name}>
                            {d.label || humanize(d.name)}
                            {d.type === "time" ? " (time)" : ""}
                          </option>
                        ))}
                      </NativeSelect>
                    </Field>
                    {isTime ? (
                      <Field label="Time grain" htmlFor="tile-grain">
                        <NativeSelect id="tile-grain" value={s.grain} onChange={(e) => set({ grain: e.target.value })}>
                          {(selectedDim?.time_grains?.length ? selectedDim.time_grains : GRAINS).map((g) => (
                            <option key={g} value={g}>
                              {humanize(g)}
                            </option>
                          ))}
                        </NativeSelect>
                      </Field>
                    ) : s.dimension ? (
                      <Field label="Top N" htmlFor="tile-limit" hint="Leave empty for all values.">
                        <Input id="tile-limit" type="number" min={1} value={s.limit} onChange={(e) => set({ limit: e.target.value })} />
                      </Field>
                    ) : null}
                  </>
                )}
              </div>
            ) : effectiveSource === "saved_query" ? (
              <Field label="Saved query" htmlFor="tile-saved">
                {saved.isError ? (
                  <InlineError error={saved.error} />
                ) : (
                  <NativeSelect id="tile-saved" value={s.savedQueryId} onChange={(e) => set({ savedQueryId: e.target.value })}>
                    <option value="">{saved.isLoading ? "Loading…" : saved.data?.length ? "Choose a saved query…" : "No saved queries yet"}</option>
                    {(saved.data ?? []).map((q) => (
                      <option key={q.id} value={q.id}>
                        {q.name}
                      </option>
                    ))}
                  </NativeSelect>
                )}
              </Field>
            ) : (
              <Field label="Artifact" htmlFor="tile-artifact" hint="Charts and query results produced by investigations.">
                {arts.isError ? (
                  <InlineError error={arts.error} />
                ) : (
                  <NativeSelect id="tile-artifact" value={s.artifactId} onChange={(e) => set({ artifactId: e.target.value })}>
                    <option value="">{arts.isLoading ? "Loading…" : arts.data?.length ? "Choose an artifact…" : "No artifacts yet"}</option>
                    {(arts.data ?? [])
                      .filter((a) => ["query", "chart", "metric_result", "dataframe"].includes(a.kind))
                      .map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.title || `${humanize(a.kind)} ${a.id.slice(0, 8)}`}
                        </option>
                      ))}
                  </NativeSelect>
                )}
              </Field>
            )}

            {s.kind === "chart" ? (
              <Field label="Chart type" htmlFor="tile-chart-type" hint="Automatic picks the chart from the shape of the data.">
                <NativeSelect id="tile-chart-type" value={s.chartType} onChange={(e) => set({ chartType: e.target.value as ChartType | "" })}>
                  <option value="">Automatic</option>
                  {ALL_CHART_TYPES.filter((t) => t !== "kpi" && t !== "table").map((t) => (
                    <option key={t} value={t}>
                      {CHART_TYPE_LABELS[t]}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
            ) : null}

            <Field label="Title" htmlFor="tile-title">
              <Input id="tile-title" value={s.title} placeholder={defaultTitle || "Tile title"} onChange={(e) => set({ title: e.target.value })} />
            </Field>
            <InlineError error={error} />
            {!metricMode && s.kind !== "kpi" && s.kind !== "text" ? (
              <p className="text-xs text-fg-subtle">
                Saved-query and artifact tiles show their result as recorded; dashboard filters apply only to semantic metric tiles.
              </p>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <Button variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={!valid || pending}>
              {tile ? "Save tile" : "Add tile"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
