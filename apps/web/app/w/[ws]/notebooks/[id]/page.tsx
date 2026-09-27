"use client";
import * as React from "react";
import { use } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ChevronRight, ListRestart, NotebookPen, Plus } from "lucide-react";
import { notebooks, queries } from "@/lib/api/resources/workbench";
import { diagnostics } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import type { CellKind, Notebook, NotebookCell } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageHeader } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/states";
import { ExportMenu } from "@/components/export/export-menu";
import { Spinner } from "@/components/ui/spinner";
import type { CodeEditorHandle, SchemaTable } from "@/components/code/code-editor";
import { NotebookCellView } from "@/components/workbench/notebook/notebook-cell";
import {
  CELL_KINDS,
  defaultSource,
  insertPosition,
  moveCellIds,
  nextCellId,
  runAllOutcome,
  sortCells,
  staleCellIds,
} from "@/components/workbench/notebook/cells";
import { formatDateTime } from "@/lib/format";

function sandboxSummary(info: Awaited<ReturnType<typeof diagnostics.info>> | undefined): string | null {
  const sb = info?.sandbox as { available?: boolean; limits?: Record<string, unknown> } | undefined;
  if (!sb) return null;
  if (sb.available === false) return "Python sandbox unavailable on this server";
  const l = (sb.limits ?? {}) as Record<string, unknown>;
  const parts: string[] = ["Sandboxed subprocess", "no network", "scratch dir only"];
  if (l.timeout_s) parts.push(`${l.timeout_s}s timeout`);
  if (l.memory_mb ?? l.mem_mb) parts.push(`${l.memory_mb ?? l.mem_mb} MB memory`);
  return parts.join(" · ");
}

function NotebookEditor({ id }: { id: string }) {
  const { id: ws, href, canEdit } = useWorkspace();
  const qc = useQueryClient();
  const key = qk.notebook(ws, id);
  const nbQ = useQuery({ queryKey: key, queryFn: () => notebooks.get(ws, id) });
  const schemaQ = useQuery({ queryKey: qk.schema(ws), queryFn: () => queries.schema(ws) });
  const diagQ = useQuery({ queryKey: qk.diagnostics, queryFn: diagnostics.info, staleTime: 300_000, retry: false });

  const [drafts, setDrafts] = React.useState<Record<string, string>>({});
  const [running, setRunning] = React.useState<Set<string>>(new Set());
  const [runningAll, setRunningAll] = React.useState(false);
  const [deleting, setDeleting] = React.useState<NotebookCell | null>(null);
  const [title, setTitle] = React.useState<string | null>(null);
  const editors = React.useRef(new Map<string, CodeEditorHandle>());
  const timers = React.useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const pendingFocus = React.useRef<string | null>(null);

  const nb = nbQ.data;
  const cells = React.useMemo(() => sortCells(nb?.cells ?? []), [nb?.cells]);

  const patchCell = React.useCallback(
    (cell: NotebookCell) =>
      qc.setQueryData<Notebook>(key, (prev) =>
        prev ? { ...prev, cells: (prev.cells ?? []).map((c) => (c.id === cell.id ? cell : c)) } : prev,
      ),
    [qc, key],
  );

  const dirty = React.useMemo(
    () => new Set(Object.entries(drafts).filter(([cid, src]) => cells.find((c) => c.id === cid)?.source !== src).map(([cid]) => cid)),
    [drafts, cells],
  );
  const stale = React.useMemo(() => staleCellIds(cells, dirty), [cells, dirty]);

  /** Persists a cell's draft source now (awaited before runs). */
  const flush = React.useCallback(
    async (cellId: string) => {
      const t = timers.current.get(cellId);
      if (t) clearTimeout(t);
      timers.current.delete(cellId);
      const cell = (qc.getQueryData<Notebook>(key)?.cells ?? []).find((c) => c.id === cellId);
      const draft = drafts[cellId];
      if (!cell || draft === undefined || draft === cell.source) return;
      const saved = await notebooks.updateCell(ws, id, cellId, { source: draft });
      patchCell(saved);
      setDrafts((d) => {
        if (d[cellId] !== draft) return d;
        const { [cellId]: _done, ...rest } = d;
        return rest;
      });
    },
    [drafts, qc, key, ws, id, patchCell],
  );

  const onSource = (cellId: string, src: string) => {
    setDrafts((d) => ({ ...d, [cellId]: src }));
    const prev = timers.current.get(cellId);
    if (prev) clearTimeout(prev);
    timers.current.set(
      cellId,
      setTimeout(() => {
        timers.current.delete(cellId);
        notebooks
          .updateCell(ws, id, cellId, { source: src })
          .then((saved) => {
            patchCell(saved);
            setDrafts((d) => {
              if (d[cellId] !== src) return d;
              const { [cellId]: _done, ...rest } = d;
              return rest;
            });
          })
          .catch((e: Error) => toast.error("Could not save cell", { description: e.message }));
      }, 700),
    );
  };

  React.useEffect(() => {
    const t = timers.current;
    return () => t.forEach((x) => clearTimeout(x));
  }, []);

  const onConfig = async (cell: NotebookCell, config: Record<string, unknown>) => {
    patchCell({ ...cell, config });
    try {
      patchCell(await notebooks.updateCell(ws, id, cell.id, { config }));
    } catch (e) {
      toast.error("Could not save cell settings", { description: e instanceof Error ? e.message : String(e) });
      void nbQ.refetch();
    }
  };

  const runCell = async (cellId: string): Promise<boolean> => {
    setRunning((s) => new Set(s).add(cellId));
    try {
      await flush(cellId);
      const res = await notebooks.runCell(ws, id, cellId);
      patchCell(res);
      return res.status !== "error";
    } catch (e) {
      toast.error("Cell failed to run", { description: e instanceof Error ? e.message : String(e) });
      return false;
    } finally {
      setRunning((s) => {
        const n = new Set(s);
        n.delete(cellId);
        return n;
      });
    }
  };

  const addCell = useMutation({
    mutationFn: async (v: { kind: CellKind; afterId: string | null; config?: Record<string, unknown> }) =>
      notebooks.addCell(ws, id, {
        kind: v.kind,
        source: defaultSource(v.kind),
        config: v.config ?? {},
        position: insertPosition(cells, v.afterId),
      }),
    onSuccess: async (cell) => {
      pendingFocus.current = cell.id;
      await qc.invalidateQueries({ queryKey: key });
    },
    onError: (e: Error) => toast.error("Could not add cell", { description: e.message }),
  });

  const runAdvance = async (cellId: string) => {
    const cell = cells.find((c) => c.id === cellId);
    if (cell && cell.kind !== "markdown") await runCell(cellId);
    else await flush(cellId);
    const next = nextCellId(cells, cellId);
    if (next) editors.current.get(next)?.focus();
    else if (canEdit) addCell.mutate({ kind: cell?.kind === "markdown" ? "sql" : (cell?.kind ?? "sql"), afterId: cellId });
  };

  React.useEffect(() => {
    const target = pendingFocus.current;
    if (target && editors.current.has(target)) {
      editors.current.get(target)?.focus();
      pendingFocus.current = null;
    }
  });

  const runAll = async () => {
    setRunningAll(true);
    try {
      await Promise.all([...dirty].map((cid) => flush(cid)));
      const res = await notebooks.runAll(ws, id);
      qc.setQueryData(key, res.notebook);
      const outcome = runAllOutcome(res);
      if (!outcome.ok)
        toast.error(`Stopped at cell ${outcome.index}`, {
          description: outcome.error ?? "The cell raised an error; cells below it were not run.",
        });
      else toast.success(`All ${outcome.executed} cells ran top to bottom`);
    } catch (e) {
      toast.error("Run all failed", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setRunningAll(false);
    }
  };

  const move = async (cellId: string, dir: -1 | 1) => {
    const ids = moveCellIds(
      cells.map((c) => c.id),
      cellId,
      dir,
    );
    qc.setQueryData<Notebook>(key, (prev) =>
      prev ? { ...prev, cells: (prev.cells ?? []).map((c) => ({ ...c, position: ids.indexOf(c.id) })) } : prev,
    );
    try {
      qc.setQueryData(key, await notebooks.reorder(ws, id, ids));
    } catch (e) {
      toast.error("Could not reorder", { description: e instanceof Error ? e.message : String(e) });
      void nbQ.refetch();
    }
  };

  const deleteCell = async (cell: NotebookCell) => {
    setDeleting(null);
    qc.setQueryData<Notebook>(key, (prev) => (prev ? { ...prev, cells: (prev.cells ?? []).filter((c) => c.id !== cell.id) } : prev));
    try {
      await notebooks.deleteCell(ws, id, cell.id);
    } catch (e) {
      toast.error("Could not delete cell", { description: e instanceof Error ? e.message : String(e) });
      void nbQ.refetch();
    }
  };

  const rename = useMutation({
    mutationFn: (t: string) => notebooks.update(ws, id, { title: t }),
    onSuccess: (res) => {
      qc.setQueryData<Notebook>(key, (prev) => (prev ? { ...prev, title: res.title } : prev));
      void qc.invalidateQueries({ queryKey: qk.notebooks(ws) });
    },
    onError: (e: Error) => toast.error("Rename failed", { description: e.message }),
  });

  const schemaTables: SchemaTable[] = React.useMemo(
    () => (schemaQ.data?.tables ?? []).map((t) => ({ name: t.table, columns: t.columns })),
    [schemaQ.data],
  );
  const sandboxNote = sandboxSummary(diagQ.data);

  if (nbQ.isLoading) return <LoadingState variant="block" label="Loading notebook" />;
  if (nbQ.isError || !nb) return <ErrorState error={nbQ.error ?? new Error("Notebook not found")} onRetry={() => void nbQ.refetch()} />;

  const addMenu = (afterId: string | null, label = "Add cell") => (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" disabled={!canEdit || addCell.isPending}>
          <Plus /> {label}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {CELL_KINDS.map((k) => (
          <DropdownMenuItem key={k.kind} onSelect={() => addCell.mutate({ kind: k.kind, afterId })}>
            <span className="w-16 font-medium">{k.label}</span>
            <span className="text-xs text-fg-subtle">{k.description}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );

  return (
    <Page>
      <PageHeader
        breadcrumb={
          <>
            <Link href={href("/notebooks")} className="hover:text-fg">
              Notebooks
            </Link>
            <ChevronRight className="size-3" />
          </>
        }
        title={
          canEdit ? (
            <input
              aria-label="Notebook title"
              value={title ?? nb.title}
              onChange={(e) => setTitle(e.target.value)}
              onBlur={() => {
                const t = (title ?? nb.title).trim();
                if (t && t !== nb.title) rename.mutate(t);
                setTitle(null);
              }}
              onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
              className="w-full min-w-0 rounded bg-transparent px-0.5 outline-none hover:bg-bg-subtle focus:bg-bg-subtle"
            />
          ) : (
            nb.title
          )
        }
        description={nb.description || undefined}
        meta={
          <>
            <span>{cells.length} cells</span>
            {nb.updated_at ? <span>Updated {formatDateTime(nb.updated_at)}</span> : null}
            {nb.investigation_id ? (
              <Link className="text-accent hover:underline" href={href(`/investigate/${nb.investigation_id}`)}>
                Exported from an investigation
              </Link>
            ) : null}
            {dirty.size ? <span className="text-warning">Saving {dirty.size} cell{dirty.size > 1 ? "s" : ""}…</span> : null}
          </>
        }
        actions={
          <>
            <Button variant="primary" size="sm" onClick={runAll} disabled={runningAll || !cells.length}>
              {runningAll ? <Spinner className="text-accent-fg" /> : <ListRestart />} Run all
            </Button>
            {addMenu(cells.length ? cells[cells.length - 1].id : null)}
            <ExportMenu target={{ kind: "notebook", id }} formats={["ipynb", "html", "md"]} filename={nb.title.replace(/[^\w.-]+/g, "_")} />
          </>
        }
      />
      <div className="min-h-0 flex-1 overflow-auto scrollbar-thin" aria-busy={runningAll}>
        <div className="mx-auto flex max-w-5xl flex-col gap-5 px-3 py-4 sm:px-5">
          {cells.length === 0 ? (
            <EmptyState
              icon={NotebookPen}
              title="This notebook is empty"
              description="Add a SQL cell to query your data, Python for statistics, Markdown for narrative, or embed a finding with its evidence."
              action={canEdit ? addMenu(null, "Add first cell") : undefined}
            />
          ) : (
            cells.map((cell, i) => (
              <NotebookCellView
                key={cell.id}
                cell={cell}
                source={drafts[cell.id] ?? cell.source}
                cells={cells}
                index={i}
                count={cells.length}
                dirty={dirty.has(cell.id)}
                stale={stale.has(cell.id)}
                running={running.has(cell.id) || runningAll}
                canEdit={canEdit}
                schema={schemaTables}
                sandboxNote={sandboxNote}
                editorRef={(h) => {
                  if (h) editors.current.set(cell.id, h);
                  else editors.current.delete(cell.id);
                }}
                onSource={(s) => onSource(cell.id, s)}
                onConfig={(c) => void onConfig(cell, c)}
                onRun={() => void runCell(cell.id)}
                onRunAdvance={() => void runAdvance(cell.id)}
                onMove={(dir) => void move(cell.id, dir)}
                onDelete={() => ((drafts[cell.id] ?? cell.source).trim() || cell.output ? setDeleting(cell) : void deleteCell(cell))}
                onInsertBelow={(kind, config) => addCell.mutate({ kind, afterId: cell.id, config })}
              />
            ))
          )}
          {cells.length ? <p className="pb-6 text-center text-2xs text-fg-faint">⌘↵ runs a cell · ⇧↵ runs and moves to the next · Run all executes top to bottom and stops at the first error</p> : null}
        </div>
      </div>
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title="Delete this cell?"
        description="Its source and output are removed. Chart cells that use it will need a new source."
        confirmLabel="Delete cell"
        destructive
        onConfirm={() => deleting && void deleteCell(deleting)}
      />
    </Page>
  );
}

export default function NotebookPage({ params }: { params: Promise<{ ws: string; id: string }> }) {
  const { id } = use(params);
  return <NotebookEditor id={id} />;
}
