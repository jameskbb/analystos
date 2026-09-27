"use client";
import * as React from "react";
import { Suspense, use } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, Eye, LayoutDashboard, Pencil, Plus, Printer, Save } from "lucide-react";
import type { LayoutItem } from "@/lib/api/types";
import { dashboards, type DashboardFilter, type DashboardOut, type Tile, type TileInput } from "@/lib/api/resources/outputs";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader } from "@/components/shell/page";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Segmented } from "@/components/ui/tabs";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import { ExportMenu } from "@/components/export/export-menu";
import { uploadChartImages } from "@/components/charts/capture";
import { Spinner } from "@/components/ui/spinner";
import { DashboardFilterBar } from "@/components/outputs/dashboard-controls";
import { DashboardGrid } from "@/components/outputs/dashboard-grid";
import { TileDialog } from "@/components/outputs/tile-dialog";
import {
  DEFAULT_TILE_SIZE,
  MIN_TILE_SIZE,
  dateRangeFor,
  nudgeLayout,
  placeTile,
  presetFromRange,
  reconcileLayout,
  sameLayout,
  type DatePreset,
} from "@/components/outputs/model";
import { formatRelative } from "@/lib/format";

function DashboardEditor({ id }: { id: string }) {
  const { id: ws, href, canEdit } = useWorkspace();
  const params = useSearchParams();
  const qc = useQueryClient();
  const query = useQuery({ queryKey: qk.dashboard(ws, id), queryFn: () => dashboards.get(ws, id) });
  const dash = query.data;
  const [editing, setEditing] = React.useState(params.get("edit") === "1" && canEdit);
  const [layout, setLayout] = React.useState<LayoutItem[]>([]);
  const [filters, setFilters] = React.useState<DashboardFilter[]>([]);
  const [preset, setPreset] = React.useState<DatePreset>("all");
  const [custom, setCustom] = React.useState<{ start: string; end: string } | null>(null);
  const [tileDialog, setTileDialog] = React.useState<{ open: boolean; tile: Tile | null }>({ open: false, tile: null });
  const [removing, setRemoving] = React.useState<Tile | null>(null);
  const [name, setName] = React.useState("");
  const hydrated = React.useRef<string | null>(null);

  const tiles = React.useMemo(() => dash?.tiles ?? [], [dash?.tiles]);

  // Hydrate local state from the server once per dashboard version.
  React.useEffect(() => {
    if (!dash) return;
    const key = `${dash.id}:${dash.version_no}`;
    if (hydrated.current === key) return;
    const first = hydrated.current === null;
    hydrated.current = key;
    setLayout(reconcileLayout(dash.layout ?? [], tiles));
    setName(dash.name);
    if (first) {
      setFilters(dash.filters ?? []);
      const p = presetFromRange(dash.date_range);
      setPreset(p.preset);
      setCustom(p.custom);
    }
  }, [dash, tiles]);

  React.useEffect(() => {
    if (dash) setLayout((l) => reconcileLayout(l, tiles));
  }, [tiles, dash]);

  const setCache = (d: DashboardOut) => {
    hydrated.current = `${d.id}:${d.version_no}`;
    qc.setQueryData(qk.dashboard(ws, id), (old: DashboardOut | undefined) => ({ ...old, ...d, tiles: d.tiles ?? old?.tiles }));
  };

  const saveLayout = useMutation({
    mutationFn: (l: LayoutItem[]) => dashboards.update(ws, id, { layout: l }),
    onSuccess: setCache,
    onError: (e: Error) => toast.error("Layout not saved", { description: e.message }),
  });
  const saveMeta = useMutation({
    mutationFn: (body: Partial<Pick<DashboardOut, "name" | "filters" | "date_range">>) => dashboards.update(ws, id, body),
    onSuccess: (d) => {
      setCache(d);
      void qc.invalidateQueries({ queryKey: qk.dashboards(ws) });
    },
    onError: (e: Error) => toast.error("Dashboard not saved", { description: e.message }),
  });

  // Debounced layout persistence.
  const layoutTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const persistLayout = (next: LayoutItem[]) => {
    setLayout(next);
    if (!dash || sameLayout(next, dash.layout ?? [])) return;
    if (layoutTimer.current) clearTimeout(layoutTimer.current);
    layoutTimer.current = setTimeout(() => saveLayout.mutate(next), 700);
  };
  React.useEffect(() => () => {
    if (layoutTimer.current) clearTimeout(layoutTimer.current);
  }, []);

  const tileMutation = useMutation({
    mutationFn: async ({ input, tile }: { input: TileInput; tile: Tile | null }) => {
      if (tile) return { tile: await dashboards.updateTile(ws, id, tile.id, input), created: false };
      return { tile: await dashboards.addTile(ws, id, input), created: true };
    },
    onSuccess: async ({ tile, created }) => {
      setTileDialog({ open: false, tile: null });
      if (created) {
        const next = [...layout, placeTile(layout, tile.id, DEFAULT_TILE_SIZE[tile.kind])];
        setLayout(next);
        try {
          setCache(await dashboards.update(ws, id, { layout: next }));
        } catch (e) {
          toast.error("Tile added, but its position was not saved", { description: (e as Error).message });
        }
      }
      await qc.invalidateQueries({ queryKey: qk.dashboard(ws, id) });
    },
  });
  const removeTile = useMutation({
    mutationFn: (t: Tile) => dashboards.removeTile(ws, id, t.id),
    onSuccess: async (_d, t) => {
      setRemoving(null);
      const next = layout.filter((l) => l.i !== t.id);
      setLayout(next);
      await dashboards.update(ws, id, { layout: next }).then(setCache).catch(() => undefined);
      await qc.invalidateQueries({ queryKey: qk.dashboard(ws, id) });
    },
    onError: (e: Error) => toast.error("Could not remove tile", { description: e.message }),
  });

  if (query.isLoading) return <LoadingState variant="block" label="Loading dashboard" />;
  if (query.isError || !dash) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;

  const dateRange = dateRangeFor(preset, custom);
  const savedFilters = JSON.stringify(dash.filters ?? []);
  const savedDate = JSON.stringify(dash.date_range ?? null);
  const contextDirty = JSON.stringify(filters) !== savedFilters || JSON.stringify(dateRange) !== savedDate;

  return (
    <Page>
      <PageHeader
        breadcrumb={
          <Link href={href("/dashboards")} className="hover:text-fg">
            Dashboards
          </Link>
        }
        title={
          editing ? (
            <Input
              aria-label="Dashboard name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onBlur={() => name.trim() && name !== dash.name && saveMeta.mutate({ name: name.trim() })}
              className="h-8 max-w-md text-lg font-semibold"
            />
          ) : (
            dash.name
          )
        }
        description={dash.description || undefined}
        meta={
          <>
            <span className="font-mono">v{dash.version_no}</span>
            <span>Updated {formatRelative(dash.updated_at ?? dash.created_at)}</span>
            {saveLayout.isPending || saveMeta.isPending ? (
              <span className="flex items-center gap-1">
                <Spinner /> Saving
              </span>
            ) : null}
          </>
        }
        actions={
          <div className="flex items-center gap-1.5 no-print">
            {canEdit ? (
              <Segmented<"view" | "edit">
                aria-label="Mode"
                value={editing ? "edit" : "view"}
                onChange={(v) => setEditing(v === "edit")}
                options={[
                  { value: "view", label: (<><Eye /> View</>) },
                  { value: "edit", label: (<><Pencil /> Edit</>) },
                ]}
              />
            ) : null}
            {editing ? (
              <Button variant="primary" onClick={() => setTileDialog({ open: true, tile: null })}>
                <Plus /> Add tile
              </Button>
            ) : null}
            <Button variant="secondary" onClick={() => setTimeout(() => window.print(), 50)} aria-label="Print or save as PDF">
              <Printer /> Print
            </Button>
            <ExportMenu
              target={{ kind: "dashboard", id }}
              formats={["html", "pdf", "md", "json"]}
              filename={dash.name}
              beforeExport={async () => {
                const r = await uploadChartImages(ws, "dashboard_tile");
                if (r.failed) toast.warning(`${r.failed} chart image${r.failed === 1 ? "" : "s"} could not be attached`);
              }}
            />
          </div>
        }
      />
      <DashboardFilterBar
        filters={filters}
        onFiltersChange={setFilters}
        preset={preset}
        custom={custom}
        onDateChange={(p, c) => {
          setPreset(p);
          setCustom(c);
        }}
        trailing={
          canEdit && contextDirty ? (
            <Button
              size="xs"
              variant="secondary"
              disabled={saveMeta.isPending}
              onClick={() => saveMeta.mutate({ filters, date_range: dateRange })}
              title="Save the current filters and date range as this dashboard's defaults"
            >
              <Save /> Save as default
            </Button>
          ) : !contextDirty && (filters.length || dateRange) ? (
            <span className="flex items-center gap-1 text-2xs text-fg-subtle">
              <Check className="size-3" /> Saved defaults
            </span>
          ) : null
        }
      />
      <PageBody className="bg-bg-subtle print:bg-white">
        {tiles.length === 0 ? (
          <EmptyState
            icon={LayoutDashboard}
            title="This dashboard has no tiles"
            description="Add KPIs and charts bound to semantic metrics, tables from saved queries, or notes."
            action={
              canEdit ? (
                <Button
                  variant="primary"
                  onClick={() => {
                    setEditing(true);
                    setTileDialog({ open: true, tile: null });
                  }}
                >
                  <Plus /> Add a tile
                </Button>
              ) : null
            }
          />
        ) : (
          <DashboardGrid
            dashboardId={id}
            tiles={tiles}
            layout={layout}
            editing={editing}
            filters={filters}
            dateRange={dateRange}
            onLayoutChange={persistLayout}
            onEditTile={(t) => setTileDialog({ open: true, tile: t })}
            onRemoveTile={setRemoving}
            onNudge={(tileId, m) => {
              const t = tiles.find((x) => x.id === tileId);
              persistLayout(nudgeLayout(layout, tileId, m, t ? MIN_TILE_SIZE[t.kind] : undefined));
            }}
          />
        )}
      </PageBody>
      <TileDialog
        open={tileDialog.open}
        tile={tileDialog.tile}
        onOpenChange={(o) => {
          setTileDialog((s) => ({ ...s, open: o }));
          if (!o) tileMutation.reset();
        }}
        onSubmit={(input) => tileMutation.mutate({ input, tile: tileDialog.tile })}
        pending={tileMutation.isPending}
        error={tileMutation.error}
      />
      <ConfirmDialog
        open={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={`Remove “${removing?.title}”?`}
        confirmLabel="Remove"
        destructive
        pending={removeTile.isPending}
        onConfirm={() => removing && removeTile.mutate(removing)}
      />
    </Page>
  );
}

export default function DashboardPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <Suspense fallback={<LoadingState variant="block" />}>
      <DashboardEditor id={id} />
    </Suspense>
  );
}
