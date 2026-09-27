"use client";
import * as React from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, BarChart3, Pencil, Play, Plus, Trash2, X } from "lucide-react";
import type { CellKind, ChartConfig, Finding, NotebookCell } from "@/lib/api/types";
import { findings as findingsApi } from "@/lib/api/endpoints";
import { useWorkspace } from "@/components/providers/workspace";
import { CodeEditor, type CodeEditorHandle, type SchemaTable } from "@/components/code/code-editor";
import { ChartView } from "@/components/charts/chart-view";
import { Statement } from "@/components/analysis/statement";
import { EvidenceBadge } from "@/components/analysis/evidence";
import { FilterChips } from "@/components/analysis/filter-chips";
import { StatusBadge } from "@/components/analysis/status";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { Spinner } from "@/components/ui/spinner";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import { CELL_KINDS, dataSourceCells } from "./cells";
import { CellOutputView, outputTable } from "./cell-output";

const sel = "h-6 min-w-0 rounded border border-border-strong bg-bg px-1 text-xs";

export interface NotebookCellProps {
  cell: NotebookCell;
  source: string;
  cells: NotebookCell[];
  index: number;
  count: number;
  dirty: boolean;
  stale: boolean;
  running: boolean;
  canEdit: boolean;
  schema?: SchemaTable[];
  sandboxNote?: string | null;
  editorRef: (h: CodeEditorHandle | null) => void;
  onSource: (s: string) => void;
  onConfig: (c: Record<string, unknown>) => void;
  onRun: () => void;
  onRunAdvance: () => void;
  onMove: (dir: -1 | 1) => void;
  onDelete: () => void;
  onInsertBelow: (kind: CellKind, config?: Record<string, unknown>) => void;
}

function FindingCellBody({
  findingId,
  canEdit,
  onPick,
}: {
  findingId: string | null;
  canEdit: boolean;
  onPick: (id: string | null) => void;
}) {
  const { id: ws, href } = useWorkspace();
  const list = useQuery({ queryKey: ["ws", ws, "findings", "list", { notebook: true }], queryFn: () => findingsApi.list(ws, { limit: 200 }), enabled: !findingId && canEdit });
  const one = useQuery({ queryKey: ["ws", ws, "findings", findingId], queryFn: () => findingsApi.get(ws, findingId as string), enabled: !!findingId });
  if (!findingId)
    return (
      <div className="flex flex-col gap-1.5 px-3 py-2">
        <label className="text-xs text-fg-subtle" htmlFor={`pick-${findingId ?? "new"}`}>
          Choose a finding to embed with its evidence
        </label>
        {list.isLoading ? (
          <Spinner />
        ) : list.isError ? (
          <span className="text-xs text-negative">{(list.error as Error).message}</span>
        ) : (list.data ?? []).length === 0 ? (
          <span className="text-xs text-fg-subtle">
            No findings yet. Save one from an{" "}
            <Link className="text-accent hover:underline" href={href("/investigate")}>
              investigation
            </Link>
            .
          </span>
        ) : (
          <select id={`pick-${findingId ?? "new"}`} className={sel} value="" disabled={!canEdit} onChange={(e) => e.target.value && onPick(e.target.value)}>
            <option value="">Select a finding…</option>
            {(list.data ?? []).map((f: Finding) => (
              <option key={f.id} value={f.id}>
                {f.statement.slice(0, 120)}
              </option>
            ))}
          </select>
        )}
      </div>
    );
  if (one.isLoading) return <div className="px-3 py-2"><Spinner /></div>;
  if (one.isError) return <div className="px-3 py-2 text-xs text-negative">Could not load finding: {(one.error as Error).message}</div>;
  const f = one.data as Finding;
  return (
    <div className="flex flex-col gap-1.5 px-3 py-2">
      <Statement type={f.statement_type}>{f.statement}</Statement>
      <div className="flex flex-wrap items-center gap-1.5">
        <EvidenceBadge strength={f.evidence_strength} reasons={f.evidence_reasons} />
        <StatusBadge status={f.status} />
        <Link className="text-xs text-accent hover:underline" href={href(`/findings/${f.id}`)}>
          Open finding
        </Link>
        {canEdit ? (
          <Button size="xs" variant="ghost" onClick={() => onPick(null)}>
            Change
          </Button>
        ) : null}
      </div>
      <FilterChips items={f.filter_context} size="xs" />
    </div>
  );
}

export function NotebookCellView(p: NotebookCellProps) {
  const { cell } = p;
  const [editingMd, setEditingMd] = React.useState(cell.kind === "markdown" && !cell.source.trim());
  const sources = dataSourceCells(p.cells, cell.id);
  const cfg = cell.config ?? {};

  const handleKeys = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && e.shiftKey && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      e.stopPropagation();
      if (cell.kind === "markdown") setEditingMd(false);
      p.onRunAdvance();
    } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && cell.kind === "markdown") {
      e.preventDefault();
      e.stopPropagation();
      setEditingMd(false);
      p.onRun();
    }
  };

  const gutter = p.running ? "[*]" : cell.execution_count ? `[${cell.execution_count}]` : "[ ]";
  const runnable = cell.kind === "sql" || cell.kind === "python" || cell.kind === "chart" || cell.kind === "finding";

  let body: React.ReactNode;
  if (cell.kind === "markdown") {
    body = editingMd ? (
      <CodeEditor
        ref={p.editorRef}
        value={p.source}
        onChange={p.onSource}
        language="markdown"
        lineNumbers={false}
        readOnly={!p.canEdit}
        ariaLabel={`Markdown cell ${p.index + 1}`}
        minHeight={60}
        onRun={() => {
          setEditingMd(false);
          p.onRun();
        }}
      />
    ) : (
      <div
        className="prose-aos min-h-8 cursor-text px-3 py-2"
        onDoubleClick={() => p.canEdit && setEditingMd(true)}
        role="button"
        tabIndex={0}
        aria-label={`Markdown cell ${p.index + 1}. Press Enter to edit`}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && p.canEdit) {
            e.preventDefault();
            setEditingMd(true);
          }
        }}
      >
        {p.source.trim() ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{p.source}</ReactMarkdown>
        ) : (
          <span className="text-sm text-fg-faint italic">Empty markdown cell. Double-click to edit.</span>
        )}
      </div>
    );
  } else if (cell.kind === "sql" || cell.kind === "python") {
    const inputs = (cfg.inputs as Record<string, string> | undefined) ?? {};
    body = (
      <>
        {cell.kind === "python" ? (
          <div className="flex flex-wrap items-center gap-1.5 border-b border-border bg-bg-subtle px-2 py-1 text-2xs text-fg-subtle">
            <span>DataFrames:</span>
            {Object.entries(inputs).map(([name, cellId]) => (
              <span key={name} className="inline-flex items-center gap-1 rounded-sm border border-border bg-bg px-1 font-mono">
                {name} = cell {p.cells.findIndex((c) => c.id === cellId) + 1 || "?"}
                {p.canEdit ? (
                  <button
                    type="button"
                    aria-label={`Unbind ${name}`}
                    onClick={() => {
                      const { [name]: _drop, ...rest } = inputs;
                      p.onConfig({ ...cfg, inputs: rest });
                    }}
                  >
                    <X className="size-3" />
                  </button>
                ) : null}
              </span>
            ))}
            {p.canEdit && sources.some((s) => s.kind === "sql") ? (
              <select
                aria-label="Bind a SQL cell result as a DataFrame"
                className="h-5 rounded border border-border-strong bg-bg px-1 text-2xs"
                value=""
                onChange={(e) => {
                  const src = e.target.value;
                  if (!src) return;
                  const n = p.cells.findIndex((c) => c.id === src) + 1;
                  p.onConfig({ ...cfg, inputs: { ...inputs, [`df${n}`]: src } });
                }}
              >
                <option value="">Bind SQL cell…</option>
                {sources
                  .filter((s) => s.kind === "sql")
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      Cell {p.cells.findIndex((c) => c.id === s.id) + 1} as df{p.cells.findIndex((c) => c.id === s.id) + 1}
                    </option>
                  ))}
              </select>
            ) : null}
            {p.sandboxNote ? <span className="ml-auto text-fg-faint">{p.sandboxNote}</span> : null}
          </div>
        ) : null}
        <CodeEditor
          ref={p.editorRef}
          value={p.source}
          onChange={p.onSource}
          language={cell.kind}
          schema={cell.kind === "sql" ? p.schema : undefined}
          readOnly={!p.canEdit}
          onRun={p.onRun}
          ariaLabel={`${cell.kind === "sql" ? "SQL" : "Python"} cell ${p.index + 1}`}
          minHeight={48}
        />
      </>
    );
  } else if (cell.kind === "chart") {
    const sourceId = (cfg.source_cell_id as string | undefined) ?? null;
    const src = p.cells.find((c) => c.id === sourceId);
    const table = outputTable(src?.output, (cfg.dataframe as string | undefined) ?? null);
    const chart = (cfg.chart as ChartConfig | undefined) ?? (cell.output?.chart as ChartConfig | undefined) ?? undefined;
    body = (
      <div className="flex flex-col gap-2 px-3 py-2">
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <label htmlFor={`src-${cell.id}`} className="text-fg-subtle">
            Data from
          </label>
          <select
            id={`src-${cell.id}`}
            className={sel}
            value={sourceId ?? ""}
            disabled={!p.canEdit}
            onChange={(e) => p.onConfig({ ...cfg, source_cell_id: e.target.value || null, chart: null })}
          >
            <option value="">Choose an earlier cell…</option>
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                Cell {p.cells.findIndex((c) => c.id === s.id) + 1} ({s.kind.toUpperCase()})
              </option>
            ))}
          </select>
          {src?.kind === "python" && Object.keys(src.output?.dataframes ?? {}).length > 1 ? (
            <select
              aria-label="DataFrame"
              className={sel}
              value={(cfg.dataframe as string | undefined) ?? ""}
              onChange={(e) => p.onConfig({ ...cfg, dataframe: e.target.value || null })}
            >
              {Object.keys(src.output?.dataframes ?? {}).map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          ) : null}
        </div>
        {!sourceId ? (
          <p className="text-sm text-fg-subtle">Pick an earlier SQL or Python cell to chart its result.</p>
        ) : !src ? (
          <p className="text-sm text-negative">The source cell was deleted. Choose another cell.</p>
        ) : !table ? (
          <p className="text-sm text-fg-subtle">Run cell {p.cells.findIndex((c) => c.id === sourceId) + 1} first; this chart plots its result.</p>
        ) : (
          <ChartView
            result={table}
            config={chart}
            onConfigChange={(c) => p.canEdit && p.onConfig({ ...cfg, chart: c })}
            height={300}
            provenance={{ sql: src.kind === "sql" ? src.source : null, python: src.kind === "python" ? src.source : null, filters: [] }}
            exportName={`notebook-cell-${p.index + 1}`}
          />
        )}
      </div>
    );
  } else {
    body = <FindingCellBody findingId={(cfg.finding_id as string | undefined) ?? null} canEdit={p.canEdit} onPick={(id) => p.onConfig({ ...cfg, finding_id: id })} />;
  }

  return (
    <div
      className={cn(
        "group/cell relative flex gap-2 rounded-md border bg-bg focus-within:border-accent/60",
        cell.status === "error" ? "border-negative/40" : "border-border",
      )}
      onKeyDownCapture={handleKeys}
      data-cell-id={cell.id}
      data-cell-kind={cell.kind}
      aria-label={`Cell ${p.index + 1}, ${cell.kind}`}
      role="group"
    >
      <div className="w-10 shrink-0 pt-2 text-right font-mono text-2xs text-fg-faint tabular" aria-hidden>
        {cell.kind === "markdown" ? "" : gutter}
      </div>
      <div className="min-w-0 flex-1 py-1 pr-1">
        <div className="flex h-7 items-center gap-1">
          <span className="text-2xs font-semibold tracking-wider text-fg-subtle uppercase">{CELL_KINDS.find((k) => k.kind === cell.kind)?.label}</span>
          {p.dirty ? <span className="text-2xs text-warning">edited</span> : null}
          {!p.dirty && p.stale && runnable ? (
            <Tooltip content="Inputs above changed or this cell has not run since; Run all recomputes it.">
              <span className="text-2xs text-fg-faint">stale</span>
            </Tooltip>
          ) : null}
          {cell.status === "error" ? <StatusBadge status="error" /> : null}
          {cell.last_run_at ? <span className="text-2xs text-fg-faint">ran {formatRelative(cell.last_run_at)}</span> : null}
          {cell.output?.elapsed_ms ? <span className="text-2xs text-fg-faint tabular">· {Math.round(cell.output.elapsed_ms)} ms</span> : null}
          <div className="ml-auto flex items-center gap-0.5 opacity-60 group-hover/cell:opacity-100 group-focus-within/cell:opacity-100">
            {cell.kind === "markdown" && p.canEdit && !editingMd ? (
              <Button size="icon-xs" variant="ghost" aria-label="Edit markdown" onClick={() => setEditingMd(true)}>
                <Pencil />
              </Button>
            ) : null}
            {runnable || (cell.kind === "markdown" && editingMd) ? (
              <Tooltip content="Run cell (⌘↵). Shift+Enter runs and moves on.">
                <Button
                  size="icon-xs"
                  variant="ghost"
                  aria-label="Run cell"
                  disabled={p.running}
                  onClick={() => {
                    if (cell.kind === "markdown") setEditingMd(false);
                    p.onRun();
                  }}
                >
                  {p.running ? <Spinner /> : <Play />}
                </Button>
              </Tooltip>
            ) : null}
            {cell.kind === "sql" && cell.output?.result && p.canEdit ? (
              <Tooltip content="Chart this result in a new cell">
                <Button size="icon-xs" variant="ghost" aria-label="Chart this result" onClick={() => p.onInsertBelow("chart", { source_cell_id: cell.id })}>
                  <BarChart3 />
                </Button>
              </Tooltip>
            ) : null}
            {p.canEdit ? (
              <>
                <Button size="icon-xs" variant="ghost" aria-label="Move cell up" disabled={p.index === 0} onClick={() => p.onMove(-1)}>
                  <ArrowUp />
                </Button>
                <Button size="icon-xs" variant="ghost" aria-label="Move cell down" disabled={p.index === p.count - 1} onClick={() => p.onMove(1)}>
                  <ArrowDown />
                </Button>
                <Button size="icon-xs" variant="ghost" aria-label="Delete cell" onClick={p.onDelete}>
                  <Trash2 />
                </Button>
              </>
            ) : null}
          </div>
        </div>
        <div className="overflow-hidden rounded border border-border">{body}</div>
        {(cell.kind === "sql" || cell.kind === "python") && cell.output ? <CellOutputView output={cell.output} className="mt-2" /> : null}
        {(cell.kind === "chart" || cell.kind === "finding") && cell.output?.error ? <CellOutputView output={{ error: cell.output.error }} className="mt-2" /> : null}
      </div>
      {p.canEdit ? (
        <div className="absolute -bottom-3 left-1/2 z-[1] flex -translate-x-1/2 gap-0.5 rounded border border-border bg-bg px-0.5 opacity-0 shadow-pop transition-opacity group-hover/cell:opacity-100 focus-within:opacity-100">
          {CELL_KINDS.map((k) => (
            <button
              key={k.kind}
              type="button"
              onClick={() => p.onInsertBelow(k.kind)}
              className="flex h-5 items-center gap-0.5 rounded-sm px-1 text-2xs text-fg-subtle hover:bg-bg-muted hover:text-fg"
              title={`Insert ${k.label} cell below: ${k.description}`}
            >
              <Plus className="size-2.5" /> {k.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
