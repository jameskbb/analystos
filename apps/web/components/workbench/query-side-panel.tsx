"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { GitCompare, History, Play, Bookmark, Trash2, FileInput, X } from "lucide-react";
import { queries, type QueryRunOut, type SavedQueryOut } from "@/lib/api/resources/workbench";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { StatusBadge } from "@/components/analysis/status";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/states";
import { Tooltip } from "@/components/ui/tooltip";
import { formatDuration, formatInt, formatRelative } from "@/lib/format";
import { CompareRunsDialog } from "./compare-dialog";
import { cn } from "@/lib/utils";

function firstLine(sql: string): string {
  const line = sql
    .split("\n")
    .map((l) => l.trim())
    .find((l) => l && !l.startsWith("--"));
  return line ?? sql.trim();
}

function HistoryList({
  onOpen,
  onRerun,
}: {
  onOpen: (run: QueryRunOut) => void;
  onRerun: (run: QueryRunOut) => void;
}) {
  const { id: ws } = useWorkspace();
  const [status, setStatus] = React.useState<string>("");
  const query = useQuery({
    queryKey: qk.queryHistory(ws, { status }),
    queryFn: () => queries.historyPage(ws, { limit: 100, status: status || undefined }),
  });
  const [selected, setSelected] = React.useState<string[]>([]);
  const [comparing, setComparing] = React.useState(false);
  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s.slice(-1), id]));

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-8 shrink-0 items-center gap-1.5 border-b border-border px-2">
        <select
          aria-label="Filter history by status"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="h-6 rounded border border-border-strong bg-bg px-1 text-xs"
        >
          <option value="">All runs</option>
          <option value="succeeded">Succeeded</option>
          <option value="failed">Failed</option>
          <option value="rejected">Rejected</option>
        </select>
        <Button
          size="xs"
          variant="secondary"
          className="ml-auto"
          disabled={selected.length !== 2}
          onClick={() => setComparing(true)}
          title="Select two successful runs to compare"
        >
          <GitCompare /> Compare {selected.length ? `(${selected.length}/2)` : ""}
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto scrollbar-thin">
        {query.isLoading ? (
          <LoadingState className="px-2" />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} compact />
        ) : !query.data?.items.length ? (
          <EmptyState compact icon={History} title="No query history yet" description="Every run, including failures and rejections, is recorded here." />
        ) : (
          <ul className="divide-y divide-border" aria-label="Query history">
            {query.data.items.map((r) => (
              <li key={r.id} className="group px-2 py-1.5 hover:bg-bg-subtle">
                <div className="flex items-center gap-1.5">
                  <Checkbox
                    checked={selected.includes(r.id)}
                    onCheckedChange={() => toggle(r.id)}
                    disabled={r.status !== "succeeded"}
                    aria-label="Select for comparison"
                  />
                  <StatusBadge status={r.status} />
                  <span className="text-2xs text-fg-subtle">{formatRelative(r.created_at)}</span>
                  {r.origin !== "sql" ? <span className="text-2xs text-fg-faint">{r.origin}</span> : null}
                  <span className="ml-auto flex opacity-60 group-hover:opacity-100 group-focus-within:opacity-100">
                    <Tooltip content="Open in editor">
                      <Button variant="ghost" size="icon-xs" aria-label="Open in editor" onClick={() => onOpen(r)}>
                        <FileInput />
                      </Button>
                    </Tooltip>
                    <Tooltip content="Re-run">
                      <Button variant="ghost" size="icon-xs" aria-label="Re-run" onClick={() => onRerun(r)}>
                        <Play />
                      </Button>
                    </Tooltip>
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => onOpen(r)}
                  className="mt-0.5 block w-full truncate text-left font-mono text-xs text-fg"
                  title={r.sql}
                >
                  {firstLine(r.sql)}
                </button>
                <div className="mt-0.5 text-2xs text-fg-subtle tabular">
                  {r.status === "succeeded"
                    ? `${formatInt(r.row_count)} rows · ${formatDuration(r.elapsed_ms)}`
                    : <span className="text-negative">{r.error ?? r.status}</span>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
      {comparing && selected.length === 2 ? (
        <CompareRunsDialog
          open={comparing}
          onOpenChange={setComparing}
          leftId={selected[0]}
          rightId={selected[1]}
        />
      ) : null}
    </div>
  );
}

function SavedList({ onOpen, currentId }: { onOpen: (q: SavedQueryOut) => void; currentId?: string | null }) {
  const { id: ws, canEdit } = useWorkspace();
  const qc = useQueryClient();
  const query = useQuery({ queryKey: qk.savedQueries(ws), queryFn: () => queries.saved(ws) });
  const [q, setQ] = React.useState("");
  const [deleting, setDeleting] = React.useState<SavedQueryOut | null>(null);
  const del = useMutation({
    mutationFn: (id: string) => queries.deleteSaved(ws, id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.savedQueries(ws) });
      toast.success("Saved query deleted");
      setDeleting(null);
    },
    onError: (e: Error) => toast.error("Delete failed", { description: e.message }),
  });
  const items = (query.data ?? []).filter(
    (s) => !q.trim() || `${s.name} ${s.description} ${s.tags.join(" ")}`.toLowerCase().includes(q.trim().toLowerCase()),
  );
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-8 shrink-0 items-center border-b border-border px-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter saved queries"
          aria-label="Filter saved queries"
          className="h-full w-full bg-transparent text-sm outline-none placeholder:text-fg-faint"
        />
      </div>
      <div className="min-h-0 flex-1 overflow-auto scrollbar-thin">
        {query.isLoading ? (
          <LoadingState className="px-2" />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} compact />
        ) : !items.length ? (
          <EmptyState
            compact
            icon={Bookmark}
            title={query.data?.length ? "No saved queries match" : "No saved queries"}
            description={query.data?.length ? undefined : "Save a query with ⌘S to reuse it, parameterize it and put it on dashboards."}
          />
        ) : (
          <ul className="divide-y divide-border" aria-label="Saved queries">
            {items.map((s) => (
              <li key={s.id} className={cn("group flex items-start gap-1 px-2 py-1.5 hover:bg-bg-subtle", s.id === currentId && "bg-accent-soft/60")}>
                <button type="button" onClick={() => onOpen(s)} className="min-w-0 flex-1 text-left">
                  <div className="truncate text-sm font-medium">{s.name}</div>
                  {s.description ? <div className="truncate text-xs text-fg-subtle">{s.description}</div> : null}
                  <div className="mt-0.5 flex flex-wrap gap-1 text-2xs text-fg-faint">
                    <span>v{s.version_no}</span>
                    {s.parameters.length ? <span>· {s.parameters.length} params</span> : null}
                    <span>· {formatRelative(s.updated_at)}</span>
                  </div>
                </button>
                {canEdit ? (
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                    aria-label={`Delete ${s.name}`}
                    onClick={() => setDeleting(s)}
                  >
                    <Trash2 />
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete “${deleting?.name}”?`}
        description="Dashboard tiles or reports that reference this saved query will stop updating."
        confirmLabel="Delete"
        destructive
        pending={del.isPending}
        onConfirm={() => deleting && del.mutate(deleting.id)}
      />
    </div>
  );
}

/** Right-hand drawer with History and Saved queries. */
export function QuerySidePanel({
  tab,
  onTabChange,
  onClose,
  onOpenRun,
  onRerun,
  onOpenSaved,
  currentSavedId,
}: {
  tab: "history" | "saved";
  onTabChange: (t: "history" | "saved") => void;
  onClose: () => void;
  onOpenRun: (r: QueryRunOut) => void;
  onRerun: (r: QueryRunOut) => void;
  onOpenSaved: (q: SavedQueryOut) => void;
  currentSavedId?: string | null;
}) {
  return (
    <Tabs value={tab} onValueChange={(v) => onTabChange(v as "history" | "saved")} className="flex h-full min-h-0 flex-col">
      <div className="flex items-center border-b border-border pr-1">
        <TabsList className="border-b-0">
          <TabsTrigger value="history">
            <History /> History
          </TabsTrigger>
          <TabsTrigger value="saved">
            <Bookmark /> Saved
          </TabsTrigger>
        </TabsList>
        <Button variant="ghost" size="icon-xs" className="ml-auto" onClick={onClose} aria-label="Close side panel">
          <X />
        </Button>
      </div>
      <TabsContent value="history" className="min-h-0 flex-1">
        <HistoryList onOpen={onOpenRun} onRerun={onRerun} />
      </TabsContent>
      <TabsContent value="saved" className="min-h-0 flex-1">
        <SavedList onOpen={onOpenSaved} currentId={currentSavedId} />
      </TabsContent>
    </Tabs>
  );
}
