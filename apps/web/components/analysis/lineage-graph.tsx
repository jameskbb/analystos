"use client";
import * as React from "react";
import Link from "next/link";
import {
  Lightbulb,
  BarChart3,
  FileCode2,
  Sigma,
  Database,
  HardDrive,
  GitBranch,
  LayoutDashboard,
  FileText,
  Columns3,
  Box,
} from "lucide-react";
import type { LineageGraph, LineageNode } from "@/lib/api/types";
import { layoutLineage } from "@/lib/lineage-layout";
import { useOptionalWorkspace } from "@/components/providers/workspace";
import { cn } from "@/lib/utils";

const KIND: Record<string, { icon: React.ComponentType<{ className?: string }>; label: string }> = {
  finding: { icon: Lightbulb, label: "Finding" },
  chart: { icon: BarChart3, label: "Chart" },
  query: { icon: FileCode2, label: "Query" },
  artifact: { icon: FileCode2, label: "Artifact" },
  metric: { icon: Sigma, label: "Metric" },
  metric_version: { icon: Sigma, label: "Metric version" },
  dimension: { icon: Columns3, label: "Dimension" },
  dataset: { icon: Database, label: "Dataset" },
  dataset_version: { icon: Database, label: "Dataset version" },
  table: { icon: Database, label: "Table" },
  column: { icon: Columns3, label: "Column" },
  source: { icon: HardDrive, label: "Source" },
  investigation: { icon: GitBranch, label: "Investigation" },
  dashboard: { icon: LayoutDashboard, label: "Dashboard" },
  tile: { icon: LayoutDashboard, label: "Tile" },
  report: { icon: FileText, label: "Report" },
};

export function lineageHref(n: LineageNode): string | null {
  const id = n.ref_id;
  if (!id) return null;
  switch (n.kind) {
    case "finding":
      return `/findings/${id}`;
    case "metric":
    case "metric_version":
      return `/metrics/${id}`;
    case "dataset":
    case "dataset_version":
    case "table":
      return `/data/${id}`;
    case "investigation":
      return `/investigate/${id}`;
    case "dashboard":
      return `/dashboards/${id}`;
    case "report":
      return `/reports/${id}`;
    case "query":
    case "artifact":
    case "chart":
      return `/lineage/artifact/${id}`;
    default:
      return null;
  }
}

const NW = 196;
const NH = 52;

/**
 * Lineage view (spec §26, §62): Finding → Chart → Query → Metric(version) → Dataset → Source.
 * Laid out left to right; every node links to its screen when it has one.
 */
export function LineageGraphView({
  graph,
  className,
  highlightId,
}: {
  graph: LineageGraph;
  className?: string;
  highlightId?: string;
}) {
  const ws = useOptionalWorkspace();
  const layout = React.useMemo(() => layoutLineage(graph, { nodeWidth: NW, nodeHeight: NH, gapX: 44, gapY: 14 }), [graph]);
  if (!graph.nodes.length)
    return <div className="px-3 py-6 text-center text-sm text-fg-subtle">No lineage recorded for this object.</div>;
  const pad = 8;
  const W = layout.width + pad * 2;
  const H = layout.height + pad * 2;
  return (
    <div className={cn("overflow-auto scrollbar-thin", className)} role="group" aria-label="Lineage graph">
      <div className="relative" style={{ width: W, height: H }}>
        <svg width={W} height={H} className="absolute inset-0" aria-hidden>
          <defs>
            <marker id="lin-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
              <path d="M0,0 L8,4 L0,8 z" fill="var(--fg-faint)" />
            </marker>
          </defs>
          {graph.edges.map((e, i) => {
            const a = layout.nodes.get(e.from);
            const b = layout.nodes.get(e.to);
            if (!a || !b) return null;
            const x1 = a.x + NW + pad;
            const y1 = a.y + NH / 2 + pad;
            const x2 = b.x + pad - 2;
            const y2 = b.y + NH / 2 + pad;
            const mx = (x1 + x2) / 2;
            return (
              <g key={i}>
                <path
                  d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`}
                  fill="none"
                  stroke="var(--border-strong)"
                  strokeWidth={1.25}
                  markerEnd="url(#lin-arrow)"
                />
                {e.label ? (
                  <text x={mx} y={(y1 + y2) / 2 - 4} textAnchor="middle" fontSize="10" fill="var(--fg-subtle)">
                    {e.label}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
        {graph.nodes.map((n) => {
          const pos = layout.nodes.get(n.id);
          if (!pos) return null;
          const meta = KIND[n.kind] ?? { icon: Box, label: n.kind };
          const href = lineageHref(n);
          const body = (
            <>
              <div className="flex items-center gap-1.5 text-2xs tracking-wide text-fg-subtle uppercase">
                <meta.icon className="size-3" />
                {meta.label}
              </div>
              <div className="truncate text-sm font-medium" title={n.label}>
                {n.label}
              </div>
              {n.detail ? (
                <div className="truncate font-mono text-2xs text-fg-subtle" title={n.detail}>
                  {n.detail}
                </div>
              ) : null}
            </>
          );
          const cls = cn(
            "absolute flex flex-col justify-center rounded border bg-bg px-2 py-1",
            n.id === highlightId || n.id === graph.root ? "border-accent shadow-[0_0_0_1px_var(--accent)]" : "border-border-strong",
            href && "hover:border-accent",
          );
          const style = { left: pos.x + pad, top: pos.y + pad, width: NW, height: NH };
          return href && ws ? (
            <Link key={n.id} href={ws.href(href)} className={cls} style={style}>
              {body}
            </Link>
          ) : (
            <div key={n.id} className={cls} style={style}>
              {body}
            </div>
          );
        })}
      </div>
    </div>
  );
}
