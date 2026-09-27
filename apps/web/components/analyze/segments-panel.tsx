"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Play } from "lucide-react";
import {
  analysis,
  type AnalysisOut,
  type ClusterResult,
  type QuadrantResult,
  type RFMResult,
  type SegmentSummary,
  type SegmentsBody,
} from "@/lib/api/resources/analysis";
import { dimensions as dimensionsApi } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { EChart, useThemeVersion } from "@/components/charts/echart";
import { readPalette } from "@/lib/charts/option";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/label";
import { Input, NativeSelect } from "@/components/ui/input";
import { Segmented } from "@/components/ui/tabs";
import { Spinner } from "@/components/ui/spinner";
import { Panel } from "@/components/shell/page";
import { InlineError } from "@/components/states/states";
import { DataGrid } from "@/components/data-grid/data-grid";
import { exclusiveEnd } from "@/components/workbench/explore/build";
import { formatInt, formatShare, formatValue } from "@/lib/format";
import { quadrantOption } from "./charts";
import { ResultMeta } from "./result-meta";
import { ColumnInput, TabularSourceForm, tabularSource, useMetricOptions, useTabularColumns, type TabularDraft, useAnalysisRun } from "./sources";

type Method = "rfm" | "product_quadrants" | "kmeans";

export const RFM_SQL = `SELECT o.customer_id, o.order_id, o.order_date, l.net_amount
FROM orders o JOIN order_lines l USING (order_id)
WHERE o.status <> 'cancelled'`;

export function isRfm(r: unknown): r is RFMResult {
  return !!r && typeof r === "object" && "customers" in r && "as_of" in r;
}
export function isQuadrant(r: unknown): r is QuadrantResult {
  return !!r && typeof r === "object" && "rows" in r && "growth_threshold" in r;
}
export function isCluster(r: unknown): r is ClusterResult {
  return !!r && typeof r === "object" && "centroids" in r && "k" in r;
}

function SegmentTable({ rows, valueLabel = "Value" }: { rows: SegmentSummary[]; valueLabel?: string }) {
  return (
    <table className="w-full text-sm" aria-label="Segments">
      <thead className="text-left text-xs text-fg-subtle">
        <tr>
          <th className="px-3 py-1.5 font-medium">Segment</th>
          <th className="px-3 py-1.5 text-right font-medium">Count</th>
          <th className="px-3 py-1.5 text-right font-medium">Share</th>
          <th className="px-3 py-1.5 text-right font-medium">{valueLabel}</th>
          <th className="px-3 py-1.5 text-right font-medium">Share of value</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((s) => (
          <tr key={s.segment} className="border-t border-border">
            <td className="px-3 py-1.5">{s.segment}</td>
            <td className="px-3 py-1.5 text-right tabular">{formatInt(s.count)}</td>
            <td className="px-3 py-1.5 text-right tabular">{formatShare(s.share)}</td>
            <td className="px-3 py-1.5 text-right tabular">{s.value === null || s.value === undefined ? "n/a" : formatValue(s.value, "number", { compact: true })}</td>
            <td className="px-3 py-1.5 text-right tabular">{s.value_share === null || s.value_share === undefined ? "n/a" : formatShare(s.value_share)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function SegmentsView({ out }: { out: AnalysisOut<RFMResult | QuadrantResult | ClusterResult> }) {
  const r = out.result;
  const theme = useThemeVersion();
  const quadOption = React.useMemo(
    () => (isQuadrant(r) ? quadrantOption(r, readPalette()) : null),
    // theme forces a rebuild with the new palette
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [r, theme],
  );
  return (
    <ResultMeta out={out}>
      {isRfm(r) ? (
        <>
          <Panel title="Segments" description={`As of ${r.as_of}; ${r.quantiles} quantile bins per score`}>
            <SegmentTable rows={r.segments} valueLabel="Monetary" />
          </Panel>
          <Panel title="Rules">
            <ul className="px-3 py-2 text-sm">
              {r.rules.map(([name, rule]) => (
                <li key={name}>
                  <span className="font-medium">{name}</span>: <span className="font-mono text-xs">{rule}</span>
                </li>
              ))}
            </ul>
          </Panel>
          <Panel title={`Customers (${formatInt(r.customers.length)} shown)`}>
            <DataGrid
              height={280}
              ariaLabel="Customer scores"
              columns={[
                { name: "customer", type: "VARCHAR" },
                { name: "segment", type: "VARCHAR" },
                { name: "recency_days", type: "INTEGER" },
                { name: "frequency", type: "INTEGER" },
                { name: "monetary", type: "DOUBLE" },
                { name: "r", type: "INTEGER" },
                { name: "f", type: "INTEGER" },
                { name: "m", type: "INTEGER" },
              ]}
              rows={r.customers.map((c) => [c.customer, c.segment, c.recency_days, c.frequency, c.monetary, c.r, c.f, c.m])}
            />
          </Panel>
        </>
      ) : null}
      {isQuadrant(r) && quadOption ? (
        <>
          <EChart option={quadOption} ariaLabel="Growth versus profitability" className="h-72 w-full" />
          <Panel title="Quadrants" description={`Thresholds: growth ${formatValue(r.growth_threshold, "percent")}, profitability ${formatValue(r.profitability_threshold, "percent")}`}>
            <SegmentTable rows={r.segments} valueLabel="Volume" />
          </Panel>
          <Panel title="Items">
            <DataGrid
              height={260}
              ariaLabel="Items by quadrant"
              columns={[
                { name: "item", type: "VARCHAR" },
                { name: "quadrant", type: "VARCHAR" },
                { name: "growth", type: "DOUBLE" },
                { name: "profitability", type: "DOUBLE" },
                { name: "volume", type: "DOUBLE" },
              ]}
              rows={r.rows.map((x) => [x.item, x.quadrant, x.growth, x.profitability, x.volume])}
              formatters={{
                growth: (v) => formatValue(v, "percent"),
                profitability: (v) => formatValue(v, "percent"),
              }}
            />
          </Panel>
        </>
      ) : null}
      {isCluster(r) ? (
        <Panel title={`${r.k} clusters`} description={r.silhouette === null ? undefined : `Silhouette ${r.silhouette.toFixed(2)} (closer to 1 = better separated)`}>
          <table className="w-full text-sm" aria-label="Cluster centroids">
            <thead className="text-left text-xs text-fg-subtle">
              <tr>
                <th className="px-3 py-1.5 font-medium">Cluster</th>
                <th className="px-3 py-1.5 text-right font-medium">Size</th>
                {r.features.map((f) => (
                  <th key={f} className="px-3 py-1.5 text-right font-medium">
                    mean {f}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {r.centroids.map((c, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="px-3 py-1.5">#{i + 1}</td>
                  <td className="px-3 py-1.5 text-right tabular">{formatInt(r.sizes[i])}</td>
                  {r.features.map((f) => (
                    <td key={f} className="px-3 py-1.5 text-right tabular">
                      {formatValue(c[f], "number", { compact: true })}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ) : null}
    </ResultMeta>
  );
}

export function SegmentsPanel({ defaultWindow }: { defaultWindow: { start: string; end: string } }) {
  const { id: ws } = useWorkspace();
  const { metrics } = useMetricOptions();
  const dims = useQuery({ queryKey: qk.dimensions(ws), queryFn: () => dimensionsApi.list(ws) });
  const [method, setMethod] = React.useState<Method>("rfm");
  // No single demo table holds customer, date and amount, so RFM starts from a join (editable).
  const [src, setSrc] = React.useState<TabularDraft>({ mode: "sql", table: "", sql: RFM_SQL });
  const cols = useTabularColumns(src);
  const [rfm, setRfm] = React.useState({ customer_col: "customer_id", date_col: "order_date", amount_col: "net_amount", order_col: "order_id", quantiles: 5 });
  const [quad, setQuad] = React.useState({
    dimension: "",
    growth_metric_id: "revenue",
    profitability_metric_id: "gross_margin_pct",
    volume_metric_id: "revenue",
    start: defaultWindow.start,
    end: defaultWindow.end,
  });
  const [km, setKm] = React.useState({ features: "", id_column: "", k: 4 });

  const body = (): SegmentsBody => {
    if (method === "rfm")
      return {
        method: "rfm",
        source: tabularSource(src),
        customer_col: rfm.customer_col,
        date_col: rfm.date_col,
        amount_col: rfm.amount_col,
        ...(rfm.order_col ? { order_col: rfm.order_col } : {}),
        quantiles: rfm.quantiles,
      };
    if (method === "product_quadrants")
      return {
        method: "product_quadrants",
        dimension: quad.dimension,
        growth_metric_id: quad.growth_metric_id,
        profitability_metric_id: quad.profitability_metric_id,
        ...(quad.volume_metric_id ? { volume_metric_id: quad.volume_metric_id } : {}),
        current: { start: quad.start, end: exclusiveEnd(quad.end) },
      };
    return {
      method: "kmeans",
      source: tabularSource(src),
      features: km.features.split(",").map((f) => f.trim()).filter(Boolean),
      ...(km.id_column ? { id_column: km.id_column } : {}),
      k: km.k,
      standardize: true,
    };
  };
  const run = useAnalysisRun(() => analysis.segments(ws, body()));
  const ready =
    method === "rfm"
      ? !!((src.mode === "table" ? src.table : src.sql.trim()) && rfm.customer_col && rfm.date_col && rfm.amount_col)
      : method === "product_quadrants"
        ? !!(quad.dimension && quad.growth_metric_id && quad.profitability_metric_id)
        : !!(km.features.trim() && (src.mode === "table" ? src.table : src.sql.trim()));
  const metricOpts = metrics.map((m) => (
    <option key={m.id} value={m.id}>
      {m.label || m.name}
    </option>
  ));

  return (
    <div className="flex flex-col gap-4">
      <Panel title="Segment customers or products" description="Rule-based segments first; k-means clustering is exploratory.">
        <form
          className="flex flex-col gap-3 p-3"
          onSubmit={(e) => {
            e.preventDefault();
            run.mutate();
          }}
        >
          <Segmented<Method>
            aria-label="Segmentation method"
            value={method}
            onChange={setMethod}
            options={[
              { value: "rfm", label: "Customers: RFM" },
              { value: "product_quadrants", label: "Products: growth × profitability" },
              { value: "kmeans", label: "Clustering (k-means)" },
            ]}
          />
          {method === "rfm" ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <div className="sm:col-span-2 lg:col-span-3">
                <TabularSourceForm value={src} onChange={setSrc} />
              </div>
              <Field label="Customer column" htmlFor="rfm-c">
                <ColumnInput id="rfm-c" columns={cols} value={rfm.customer_col} onChange={(v) => setRfm({ ...rfm, customer_col: v })} />
              </Field>
              <Field label="Date column" htmlFor="rfm-d">
                <ColumnInput id="rfm-d" columns={cols} value={rfm.date_col} onChange={(v) => setRfm({ ...rfm, date_col: v })} />
              </Field>
              <Field label="Amount column (monetary)" htmlFor="rfm-a">
                <ColumnInput id="rfm-a" columns={cols} numericOnly value={rfm.amount_col} onChange={(v) => setRfm({ ...rfm, amount_col: v })} />
              </Field>
              <Field label="Order id column (optional)" htmlFor="rfm-o" hint="Frequency counts distinct orders when set, rows otherwise">
                <ColumnInput id="rfm-o" columns={cols} value={rfm.order_col} onChange={(v) => setRfm({ ...rfm, order_col: v })} />
              </Field>
              <Field label="Quantile bins" htmlFor="rfm-q">
                <Input id="rfm-q" type="number" min={2} max={10} value={rfm.quantiles} onChange={(e) => setRfm({ ...rfm, quantiles: Number(e.target.value) || 5 })} />
              </Field>
            </div>
          ) : null}
          {method === "product_quadrants" ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <Field label="Items (dimension)" htmlFor="q-dim">
                <NativeSelect id="q-dim" value={quad.dimension} onChange={(e) => setQuad({ ...quad, dimension: e.target.value })}>
                  <option value="">Choose…</option>
                  {(dims.data ?? [])
                    .filter((d) => d.type !== "time")
                    .map((d) => (
                      <option key={d.name} value={d.name}>
                        {d.label || d.name}
                      </option>
                    ))}
                </NativeSelect>
              </Field>
              <Field label="Growth of" htmlFor="q-g">
                <NativeSelect id="q-g" value={quad.growth_metric_id} onChange={(e) => setQuad({ ...quad, growth_metric_id: e.target.value })}>
                  {metricOpts}
                </NativeSelect>
              </Field>
              <Field label="Profitability" htmlFor="q-p">
                <NativeSelect id="q-p" value={quad.profitability_metric_id} onChange={(e) => setQuad({ ...quad, profitability_metric_id: e.target.value })}>
                  {metricOpts}
                </NativeSelect>
              </Field>
              <Field label="Volume (optional)" htmlFor="q-v">
                <NativeSelect id="q-v" value={quad.volume_metric_id} onChange={(e) => setQuad({ ...quad, volume_metric_id: e.target.value })}>
                  <option value="">None</option>
                  {metricOpts}
                </NativeSelect>
              </Field>
              <Field label="Current period from" htmlFor="q-s" hint="Growth compares with the same length immediately before">
                <Input id="q-s" type="date" value={quad.start} onChange={(e) => setQuad({ ...quad, start: e.target.value })} />
              </Field>
              <Field label="to (inclusive)" htmlFor="q-e">
                <Input id="q-e" type="date" value={quad.end} onChange={(e) => setQuad({ ...quad, end: e.target.value })} />
              </Field>
            </div>
          ) : null}
          {method === "kmeans" ? (
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              <TabularSourceForm value={src} onChange={setSrc} />
              <div className="flex flex-col gap-3">
                <Field label="Numeric features" htmlFor="km-f" hint={cols.length ? `Available: ${cols.map((c) => c.name).join(", ")}` : "Comma-separated column names"}>
                  <Input id="km-f" value={km.features} placeholder="e.g. frequency, monetary" onChange={(e) => setKm({ ...km, features: e.target.value })} />
                </Field>
                <Field label="Id column (optional)" htmlFor="km-id">
                  <ColumnInput id="km-id" columns={cols} value={km.id_column} onChange={(v) => setKm({ ...km, id_column: v })} />
                </Field>
                <Field label="Clusters (k)" htmlFor="km-k">
                  <Input id="km-k" type="number" min={2} max={10} value={km.k} onChange={(e) => setKm({ ...km, k: Number(e.target.value) || 3 })} />
                </Field>
              </div>
            </div>
          ) : null}
          <div>
            <Button type="submit" variant="primary" disabled={!ready || run.isPending}>
              {run.isPending ? <Spinner /> : <Play />} Segment
            </Button>
          </div>
          {run.error ? <InlineError error={run.error} /> : null}
        </form>
      </Panel>
      {run.data ? (
        <Panel title="Result" bodyClassName="p-3">
          <SegmentsView key={run.data.id} out={run.data} />
        </Panel>
      ) : null}
    </div>
  );
}
