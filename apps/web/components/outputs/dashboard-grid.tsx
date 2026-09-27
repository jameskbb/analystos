"use client";
import * as React from "react";
import ReactGridLayout, { useContainerWidth, type Layout } from "react-grid-layout";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  GripVertical,
  MoreHorizontal,
  MoveDiagonal2,
  Pencil,
  ScanSearch,
  Trash2,
  Minimize2,
} from "lucide-react";
import type { LayoutItem } from "@/lib/api/types";
import { dashboards as dashboardsApi, type DashboardDateRange, type DashboardFilter, type Tile } from "@/lib/api/resources/outputs";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { ChartInspector } from "@/components/charts/chart-inspector";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { GRID_COLS, MIN_TILE_SIZE, type LayoutMove } from "./model";
import { TileBody, tileProvenance } from "./tile";

const ROW_HEIGHT = 36;

function TileFrame({
  tile,
  dashboardId,
  filters,
  dateRange,
  editing,
  onEdit,
  onRemove,
  onNudge,
}: {
  tile: Tile;
  dashboardId: string;
  filters: DashboardFilter[];
  dateRange: DashboardDateRange | null;
  editing: boolean;
  onEdit: () => void;
  onRemove: () => void;
  onNudge: (m: LayoutMove) => void;
}) {
  const { id: ws } = useWorkspace();
  const [inspect, setInspect] = React.useState(false);
  const ctx = { filters, dateRange, binding: tile.binding, viz: tile.viz };
  const data = useQuery({
    queryKey: qk.tileData(ws, dashboardId, tile.id, ctx),
    queryFn: () => dashboardsApi.tileData(ws, dashboardId, tile.id, { filters, date_range: dateRange }),
    enabled: tile.kind !== "text",
    staleTime: 60_000,
  });
  const nudges: { move: LayoutMove; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
    { move: "left", label: "Move left", icon: ArrowLeft },
    { move: "right", label: "Move right", icon: ArrowRight },
    { move: "up", label: "Move up", icon: ArrowUp },
    { move: "down", label: "Move down", icon: ArrowDown },
    { move: "wider", label: "Wider", icon: MoveDiagonal2 },
    { move: "narrower", label: "Narrower", icon: Minimize2 },
    { move: "taller", label: "Taller", icon: MoveDiagonal2 },
    { move: "shorter", label: "Shorter", icon: Minimize2 },
  ];
  return (
    <section
      aria-label={`${tile.title} (${tile.kind})`}
      tabIndex={0}
      data-print-avoid-break
      className={cn(
        "flex h-full min-h-0 flex-col overflow-hidden rounded-md border border-border bg-bg focus-visible:outline-2 focus-visible:outline-ring",
        editing && "border-dashed border-border-strong",
      )}
    >
      <header className="flex h-8 shrink-0 items-center gap-1 px-2">
        {editing ? (
          <span className="tile-drag-handle -ml-1 cursor-grab rounded p-0.5 text-fg-faint hover:text-fg active:cursor-grabbing" aria-hidden>
            <GripVertical className="size-3.5" />
          </span>
        ) : null}
        <h3 className="min-w-0 flex-1 truncate text-sm font-medium" title={tile.title}>
          {tile.title}
        </h3>
        {tile.kind !== "text" && tile.kind !== "chart" ? (
          <Button variant="ghost" size="icon-xs" className="no-print" aria-label={`Inspect ${tile.title}`} onClick={() => setInspect(true)}>
            <ScanSearch />
          </Button>
        ) : null}
        {editing ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-xs" aria-label={`Tile options for ${tile.title}`} className="no-print">
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={onEdit}>
                <Pencil /> Edit tile
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuLabel>Arrange</DropdownMenuLabel>
              {nudges.map((n) => (
                <DropdownMenuItem
                  key={n.move}
                  onSelect={(e) => {
                    e.preventDefault();
                    onNudge(n.move);
                  }}
                >
                  <n.icon /> {n.label}
                </DropdownMenuItem>
              ))}
              <DropdownMenuSeparator />
              <DropdownMenuItem destructive onSelect={onRemove}>
                <Trash2 /> Remove tile
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </header>
      <div className="min-h-0 flex-1 overflow-hidden">
        <TileBody
          dashboardId={dashboardId}
          tile={tile}
          data={data.data}
          loading={data.isLoading}
          error={data.error}
          onRetry={() => void data.refetch()}
        />
      </div>
      {tile.kind !== "text" ? (
        <ChartInspector
          open={inspect}
          onOpenChange={setInspect}
          config={tile.viz.chart ?? { type: tile.kind === "kpi" ? "kpi" : "table" }}
          provenance={tileProvenance(tile, data.data, dashboardId)}
          title={tile.title}
        />
      ) : null}
    </section>
  );
}

/** Drag/resize grid of tiles (react-grid-layout v2). In view mode the layout is static. */
export function DashboardGrid({
  dashboardId,
  tiles,
  layout,
  editing,
  filters,
  dateRange,
  onLayoutChange,
  onEditTile,
  onRemoveTile,
  onNudge,
}: {
  dashboardId: string;
  tiles: Tile[];
  layout: LayoutItem[];
  editing: boolean;
  filters: DashboardFilter[];
  dateRange: DashboardDateRange | null;
  onLayoutChange: (l: LayoutItem[]) => void;
  onEditTile: (t: Tile) => void;
  onRemoveTile: (t: Tile) => void;
  onNudge: (tileId: string, m: LayoutMove) => void;
}) {
  const { width, containerRef, mounted } = useContainerWidth();
  const narrow = mounted && width < 640;
  const gridLayout: Layout = layout.map((l) => {
    const t = tiles.find((x) => x.id === l.i);
    const min = t ? MIN_TILE_SIZE[t.kind] : { w: 1, h: 1 };
    return narrow ? { ...l, x: 0, w: GRID_COLS, minW: min.w, minH: min.h } : { ...l, minW: min.w, minH: min.h };
  });
  return (
    <div ref={containerRef as React.Ref<HTMLDivElement>} className="w-full">
      {mounted ? (
        <ReactGridLayout
          width={width}
          layout={gridLayout}
          gridConfig={{ cols: GRID_COLS, rowHeight: ROW_HEIGHT, margin: [12, 12], containerPadding: [0, 0] }}
          dragConfig={{ enabled: editing && !narrow, handle: ".tile-drag-handle" }}
          resizeConfig={{ enabled: editing && !narrow }}
          onLayoutChange={(next) => {
            if (!editing || narrow) return;
            onLayoutChange(next.map((l) => ({ i: l.i, x: l.x, y: l.y, w: l.w, h: l.h })));
          }}
        >
          {layout.map((l) => {
            const t = tiles.find((x) => x.id === l.i);
            if (!t) return <div key={l.i} />;
            return (
              <div key={l.i}>
                <TileFrame
                  tile={t}
                  dashboardId={dashboardId}
                  filters={filters}
                  dateRange={dateRange}
                  editing={editing}
                  onEdit={() => onEditTile(t)}
                  onRemove={() => onRemoveTile(t)}
                  onNudge={(m) => onNudge(t.id, m)}
                />
              </div>
            );
          })}
        </ReactGridLayout>
      ) : null}
    </div>
  );
}
