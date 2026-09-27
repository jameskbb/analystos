/** Pure notebook helpers: ordering, moves, insertion points, chart sources and staleness. */
import type { CellKind, NotebookCell, RunAllResult } from "@/lib/api/types";

export function sortCells<T extends Pick<NotebookCell, "position" | "id">>(cells: T[]): T[] {
  return [...cells].sort((a, b) => a.position - b.position || a.id.localeCompare(b.id));
}

/** New id order after moving `id` up (-1) or down (+1); unchanged at the edges. */
export function moveCellIds(ids: string[], id: string, dir: -1 | 1): string[] {
  const i = ids.indexOf(id);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= ids.length) return ids;
  const next = [...ids];
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}

/** Position for a new cell inserted after `afterId` (end when null/unknown). */
export function insertPosition(cells: Pick<NotebookCell, "id" | "position">[], afterId: string | null): number {
  const sorted = sortCells(cells as NotebookCell[]);
  if (!afterId) return sorted.length;
  const i = sorted.findIndex((c) => c.id === afterId);
  return i < 0 ? sorted.length : i + 1;
}

/** The cell after `id` in notebook order, or null at the end. */
export function nextCellId(cells: Pick<NotebookCell, "id" | "position">[], id: string): string | null {
  const sorted = sortCells(cells as NotebookCell[]);
  const i = sorted.findIndex((c) => c.id === id);
  return i >= 0 && i < sorted.length - 1 ? sorted[i + 1].id : null;
}

/** Cells above `id` that produce a tabular result a chart or Python cell can use. */
export function dataSourceCells(cells: NotebookCell[], id: string): NotebookCell[] {
  const sorted = sortCells(cells);
  const i = sorted.findIndex((c) => c.id === id);
  return sorted.slice(0, i < 0 ? sorted.length : i).filter((c) => c.kind === "sql" || c.kind === "python");
}

/**
 * Cells whose output no longer reflects the notebook: edited since their last run, never run,
 * or below an edited/failed cell (a Run all will recompute them).
 */
export function staleCellIds(cells: NotebookCell[], dirty: Set<string>): Set<string> {
  const out = new Set<string>();
  let upstreamChanged = false;
  for (const c of sortCells(cells)) {
    if (c.kind === "markdown") continue;
    const self = dirty.has(c.id) || (c.execution_count === 0 && c.kind !== "finding");
    if (self || (upstreamChanged && c.execution_count > 0)) out.add(c.id);
    if (self || c.status === "error") upstreamChanged = true;
  }
  return out;
}

export const CELL_KINDS: { kind: CellKind; label: string; description: string }[] = [
  { kind: "sql", label: "SQL", description: "Read-only DuckDB query" },
  { kind: "python", label: "Python", description: "Sandboxed pandas / numpy / scipy / statsmodels" },
  { kind: "markdown", label: "Markdown", description: "Narrative and notes" },
  { kind: "chart", label: "Chart", description: "Chart of an earlier cell's result" },
  { kind: "finding", label: "Finding", description: "Embed a saved finding with its evidence" },
];

export function defaultSource(kind: CellKind): string {
  switch (kind) {
    case "sql":
      return "select *\nfrom ";
    case "python":
      return "# `con` is a read-only DuckDB connection; bound SQL cell results are pandas DataFrames.\ndf = con.sql(\"select 1 as x\").df()\nprint(df.describe())\n";
    case "markdown":
      return "## Notes\n";
    default:
      return "";
  }
}

/** Outcome of "Run all" from the API's `RunAllResult` (stopped_at is authoritative, not cell scans). */
export function runAllOutcome(
  res: RunAllResult,
): { ok: true; executed: number } | { ok: false; executed: number; index: number; error: string | null } {
  if (!res.stopped_at) return { ok: true, executed: res.executed };
  const ordered = sortCells(res.notebook.cells ?? []);
  const index = ordered.findIndex((c) => c.id === res.stopped_at);
  const cell = index >= 0 ? ordered[index] : undefined;
  const err = cell?.output && typeof cell.output === "object" ? (cell.output as { error?: unknown }).error : null;
  return { ok: false, executed: res.executed, index: index + 1, error: typeof err === "string" ? err : null };
}
