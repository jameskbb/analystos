"use client";
import * as React from "react";
import Link from "next/link";
import { Database, Sigma, Layers, ArrowDownUp, Calculator, FileCode2, Link2 } from "lucide-react";
import type { ChartConfig, ChartProvenance } from "@/lib/api/types";
import { Sheet, SheetBody, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { CodeBlock } from "@/components/code/code-block";
import { FilterChips } from "@/components/analysis/filter-chips";
import { SectionLabel } from "@/components/shell/page";
import { CHART_TYPE_LABELS } from "@/lib/charts/suggest";
import { useOptionalWorkspace } from "@/components/providers/workspace";
import { useQuery } from "@tanstack/react-query";
import { dashboards as dashboardsApi } from "@/lib/api/endpoints";
import { LineageGraphView } from "@/components/analysis/lineage-graph";
import { ErrorState, LoadingState } from "@/components/states/states";

/** KPI/tile lineage (spec §62): tile → KPI/chart → query → metric (version) → entity → dataset → source. */
function TileLineage({ ws, dashboardId, tileId }: { ws: string; dashboardId: string; tileId: string }) {
  const q = useQuery({
    queryKey: ["ws", ws, "dashboards", dashboardId, "tile", tileId, "lineage"],
    queryFn: () => dashboardsApi.tileLineage(ws, dashboardId, tileId),
  });
  if (q.isLoading) return <LoadingState label="Loading lineage" />;
  if (q.error) return <ErrorState error={q.error} compact onRetry={() => void q.refetch()} />;
  return q.data ? <LineageGraphView graph={q.data} highlightId={q.data.root} /> : null;
}

function Row({
  icon: Icon,
  label,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex gap-2.5 border-b border-border py-2.5 last:border-b-0">
      <Icon className="mt-0.5 size-3.5 shrink-0 text-fg-subtle" />
      <div className="min-w-0 flex-1">
        <SectionLabel>{label}</SectionLabel>
        <div className="mt-1 text-sm">{children}</div>
      </div>
    </div>
  );
}

const none = <span className="text-fg-subtle">None</span>;

/**
 * Chart Inspector (spec §35): every chart exposes dataset, metric (+version), dimensions,
 * filters, sort, calculation and the executed query.
 */
export function ChartInspector({
  open,
  onOpenChange,
  config,
  provenance,
  reason,
  title,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  config: ChartConfig;
  provenance?: ChartProvenance;
  reason?: string;
  title?: string;
}) {
  const ws = useOptionalWorkspace();
  const p = provenance ?? {};
  const datasets = p.datasets?.length ? p.datasets : p.dataset ? [p.dataset] : [];
  const dims = p.dimensions?.length ? p.dimensions : [config.x, config.series].filter(Boolean) as string[];
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent width="w-[480px]" aria-describedby={undefined}>
        <SheetHeader>
          <SheetTitle>Chart inspector</SheetTitle>
          <SheetDescription>
            {title ? `${title} · ` : ""}
            {CHART_TYPE_LABELS[config.type]}
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="py-1">
          <Row icon={Database} label="Dataset">
            {datasets.length ? (
              <div className="flex flex-wrap gap-1">
                {datasets.map((d) => (
                  <code key={d} className="rounded-sm bg-bg-muted px-1 font-mono text-xs">
                    {d}
                  </code>
                ))}
              </div>
            ) : (
              <span className="text-fg-subtle">Ad-hoc query result</span>
            )}
          </Row>
          <Row icon={Sigma} label="Metric">
            {p.metric ? (
              <span>
                {p.metric}
                {p.metric_version !== undefined && p.metric_version !== null ? (
                  <span className="ml-1.5 font-mono text-xs text-fg-subtle">v{String(p.metric_version)}</span>
                ) : null}
              </span>
            ) : config.y?.length ? (
              <span>
                {config.y.join(", ")} <span className="text-xs text-fg-subtle">(result column, not a semantic metric)</span>
              </span>
            ) : (
              none
            )}
          </Row>
          <Row icon={Layers} label="Dimensions">
            {dims.length ? dims.join(", ") : none}
          </Row>
          <Row icon={Layers} label="Filters">
            <FilterChips items={p.filters ?? []} />
          </Row>
          <Row icon={ArrowDownUp} label="Sorting">
            {p.sort ?? (config.sort ? `${config.sort.field} ${config.sort.direction}` : "Result order")}
          </Row>
          <Row icon={Calculator} label="Calculation">
            {p.calculation ?? (
              <span className="text-fg-subtle">
                {config.type === "histogram"
                  ? "Client-side binning (Freedman–Diaconis) of the query result."
                  : config.type === "box"
                    ? "Client-side quartiles (linear interpolation), whiskers at 1.5×IQR."
                    : config.type === "waterfall"
                      ? "Running total of signed values; anchors are start/end rows."
                      : "Values plotted as returned by the query (duplicates summed per category)."}
              </span>
            )}
          </Row>
          {reason ? (
            <Row icon={Calculator} label="Why this chart">
              {reason}
            </Row>
          ) : null}
          <Row icon={FileCode2} label="Query">
            {p.sql ? <CodeBlock code={p.sql} maxHeight={280} /> : <span className="text-fg-subtle">No SQL recorded.</span>}
            {p.python ? <CodeBlock code={p.python} language="python" className="mt-2" maxHeight={220} /> : null}
          </Row>
          {p.tile && ws && open ? (
            <Row icon={Link2} label="Lineage">
              <TileLineage ws={ws.id} dashboardId={p.tile.dashboard_id} tileId={p.tile.tile_id} />
            </Row>
          ) : null}
          {p.artifact_id && ws ? (
            <Row icon={Link2} label="Artifact">
              <Link className="text-accent hover:underline" href={ws.href(`/lineage/artifact/${p.artifact_id}`)}>
                View lineage for artifact {p.artifact_id.slice(0, 8)}
              </Link>
            </Row>
          ) : null}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}
