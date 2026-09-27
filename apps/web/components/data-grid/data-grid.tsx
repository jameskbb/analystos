"use client";
import * as React from "react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import type { QueryColumn } from "@/lib/api/types";
import { formatCell, isNumericType, toNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

type Row = unknown[];

function estimateWidth(col: QueryColumn, rows: Row[], idx: number): number {
  let max = col.name.length + 3;
  const n = Math.min(rows.length, 200);
  for (let i = 0; i < n; i++) {
    const s = formatCell(rows[i][idx], col.type);
    if (s.length > max) max = s.length;
  }
  return Math.max(72, Math.min(360, max * 7.2 + 20));
}

function shortType(t: string): string {
  const s = t.toLowerCase();
  if (/^(big|small|tiny|hu)?int|integer|ubigint|uinteger/.test(s)) return "int";
  if (/double|float|real/.test(s)) return "float";
  if (/decimal|numeric/.test(s)) return "dec";
  if (/timestamp/.test(s)) return "ts";
  if (/date/.test(s)) return "date";
  if (/bool/.test(s)) return "bool";
  if (/varchar|text|string|char/.test(s)) return "text";
  return s.slice(0, 8);
}

/**
 * Virtualized result grid (TanStack Table + Virtual). Handles hundreds of thousands of rows,
 * client-side sort, sticky header, row numbers and NULL styling.
 */
export function DataGrid({
  columns,
  rows,
  className,
  height,
  onCellClick,
  emptyMessage = "No rows returned.",
  sortable = true,
  formatters,
  ariaLabel = "Result table",
  dense = true,
}: {
  columns: QueryColumn[];
  rows: Row[];
  className?: string;
  height?: number | string;
  onCellClick?: (row: Row, column: QueryColumn) => void;
  emptyMessage?: string;
  sortable?: boolean;
  formatters?: Record<string, (v: unknown) => React.ReactNode>;
  ariaLabel?: string;
  dense?: boolean;
}) {
  const [sorting, setSorting] = React.useState<SortingState>([]);
  const parentRef = React.useRef<HTMLDivElement>(null);
  const rowHeight = dense ? 26 : 32;

  const colDefs = React.useMemo<ColumnDef<Row>[]>(
    () =>
      columns.map((c, idx) => {
        const numeric = isNumericType(c.type);
        return {
          id: `${idx}:${c.name}`,
          accessorFn: (r: Row) => r[idx],
          header: c.name,
          size: estimateWidth(c, rows, idx),
          meta: { numeric, type: c.type, col: c },
          sortingFn: (a, b, colId) => {
            const x = a.getValue(colId);
            const y = b.getValue(colId);
            if (x === null || x === undefined) return 1;
            if (y === null || y === undefined) return -1;
            const nx = toNumber(x);
            const ny = toNumber(y);
            if (numeric && nx !== null && ny !== null) return nx - ny;
            return String(x).localeCompare(String(y));
          },
        } satisfies ColumnDef<Row>;
      }),
    [columns, rows],
  );

  const table = useReactTable({
    data: rows,
    columns: colDefs,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: sortable ? getSortedRowModel() : undefined,
    enableSorting: sortable,
  });

  const tableRows = table.getRowModel().rows;
  const virtualizer = useVirtualizer({
    count: tableRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    overscan: 12,
  });

  const gutter = Math.max(36, String(rows.length).length * 8 + 16);
  const totalWidth = gutter + table.getVisibleLeafColumns().reduce((s, c) => s + c.getSize(), 0);

  if (!columns.length) {
    return <div className="px-3 py-6 text-center text-sm text-fg-subtle">{emptyMessage}</div>;
  }

  return (
    <div
      ref={parentRef}
      role="table"
      aria-label={ariaLabel}
      aria-rowcount={rows.length + 1}
      className={cn("relative overflow-auto scrollbar-thin bg-bg text-xs", className)}
      style={{ height }}
    >
      <div style={{ width: totalWidth, minWidth: "100%" }}>
        <div role="rowgroup" className="sticky top-0 z-10 flex border-b border-border bg-bg-subtle">
          <div role="row" className="flex">
            <div
              role="columnheader"
              className="sticky left-0 z-10 flex shrink-0 items-center justify-end border-r border-border bg-bg-subtle px-2 text-2xs text-fg-faint"
              style={{ width: gutter, height: rowHeight + 4 }}
            >
              #
            </div>
            {table.getFlatHeaders().map((h) => {
              const meta = h.column.columnDef.meta as { numeric: boolean; type: string };
              const sorted = h.column.getIsSorted();
              return (
                <div
                  key={h.id}
                  role="columnheader"
                  aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none"}
                  className="flex shrink-0 items-center border-r border-border"
                  style={{ width: h.getSize(), height: rowHeight + 4 }}
                >
                  <button
                    type="button"
                    disabled={!sortable}
                    onClick={h.column.getToggleSortingHandler()}
                    className={cn(
                      "group flex h-full w-full min-w-0 items-center gap-1 px-2 text-left font-medium text-fg hover:bg-bg-muted disabled:cursor-default disabled:hover:bg-transparent",
                      meta.numeric && "flex-row-reverse text-right",
                    )}
                    title={`${String(h.column.columnDef.header)} (${meta.type})`}
                  >
                    <span className="truncate">{flexRender(h.column.columnDef.header, h.getContext())}</span>
                    <span className="shrink-0 font-mono text-2xs font-normal text-fg-faint">{shortType(meta.type)}</span>
                    {sortable ? (
                      sorted === "asc" ? (
                        <ArrowUp className="size-3 shrink-0 text-accent" />
                      ) : sorted === "desc" ? (
                        <ArrowDown className="size-3 shrink-0 text-accent" />
                      ) : (
                        <ChevronsUpDown className="size-3 shrink-0 text-fg-faint opacity-0 group-hover:opacity-100" />
                      )
                    ) : null}
                  </button>
                </div>
              );
            })}
          </div>
        </div>
        {rows.length === 0 ? (
          <div className="px-3 py-6 text-center text-sm text-fg-subtle">{emptyMessage}</div>
        ) : (
          <div role="rowgroup" style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualizer.getVirtualItems().map((vi) => {
              const row = tableRows[vi.index];
              return (
                <div
                  key={row.id}
                  role="row"
                  aria-rowindex={vi.index + 2}
                  className="absolute left-0 flex border-b border-border/70 hover:bg-bg-subtle"
                  style={{ transform: `translateY(${vi.start}px)`, height: rowHeight, width: totalWidth, minWidth: "100%" }}
                >
                  <div
                    role="rowheader"
                    className="sticky left-0 flex shrink-0 items-center justify-end border-r border-border bg-bg px-2 font-mono text-2xs text-fg-faint tabular"
                    style={{ width: gutter }}
                  >
                    {vi.index + 1}
                  </div>
                  {row.getVisibleCells().map((cell) => {
                    const meta = cell.column.columnDef.meta as { numeric: boolean; type: string; col: QueryColumn };
                    const v = cell.getValue();
                    const custom = formatters?.[meta.col.name];
                    const isNull = v === null || v === undefined;
                    return (
                      <div
                        key={cell.id}
                        role="cell"
                        onClick={onCellClick ? () => onCellClick(row.original, meta.col) : undefined}
                        className={cn(
                          "flex shrink-0 items-center overflow-hidden border-r border-border/70 px-2 whitespace-nowrap",
                          meta.numeric && "justify-end font-mono tabular",
                          isNull && "text-fg-faint italic",
                          onCellClick && "cursor-pointer",
                        )}
                        style={{ width: cell.column.getSize() }}
                        title={isNull ? "NULL" : formatCell(v, meta.type)}
                      >
                        <span className="truncate">{custom ? custom(v) : formatCell(v, meta.type)}</span>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
