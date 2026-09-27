"use client";
import * as React from "react";
import { ChevronRight, Table2, Search } from "lucide-react";
import type { SchemaTableOut } from "@/lib/api/resources/workbench";
import { formatInt } from "@/lib/format";
import { cn } from "@/lib/utils";

function shortType(t: string): string {
  const s = t.toLowerCase();
  if (/int/.test(s)) return "int";
  if (/double|float|real|decimal|numeric/.test(s)) return "num";
  if (/timestamp/.test(s)) return "ts";
  if (/date/.test(s)) return "date";
  if (/bool/.test(s)) return "bool";
  if (/char|text|string/.test(s)) return "text";
  return s.slice(0, 6);
}

/** Tables → columns tree with search. Clicking a name inserts it at the editor cursor. */
export function SchemaBrowser({
  tables,
  onInsert,
  className,
}: {
  tables: SchemaTableOut[];
  onInsert: (text: string) => void;
  className?: string;
}) {
  const [q, setQ] = React.useState("");
  const [open, setOpen] = React.useState<Set<string>>(new Set());
  const query = q.trim().toLowerCase();
  const visible = tables
    .map((t) => {
      if (!query) return { t, cols: t.columns, forceOpen: false };
      const tableHit = t.table.toLowerCase().includes(query) || (t.dataset_name ?? "").toLowerCase().includes(query);
      const cols = t.columns.filter((c) => c.name.toLowerCase().includes(query));
      if (!tableHit && !cols.length) return null;
      return { t, cols: tableHit ? t.columns : cols, forceOpen: cols.length > 0 };
    })
    .filter((x): x is { t: SchemaTableOut; cols: SchemaTableOut["columns"]; forceOpen: boolean } => x !== null);

  const toggle = (name: string) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(name)) n.delete(name);
      else n.add(name);
      return n;
    });

  return (
    <div className={cn("flex h-full min-h-0 flex-col", className)}>
      <div className="flex h-9 shrink-0 items-center gap-1.5 border-b border-border px-2">
        <Search className="size-3.5 shrink-0 text-fg-subtle" aria-hidden />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter tables and columns"
          aria-label="Filter schema"
          className="h-full min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-fg-faint"
        />
      </div>
      <ul className="min-h-0 flex-1 overflow-auto py-1 text-xs scrollbar-thin" role="tree" aria-label="Schema">
        {visible.length === 0 ? (
          <li className="px-3 py-4 text-center text-fg-subtle">No tables match.</li>
        ) : (
          visible.map(({ t, cols, forceOpen }) => {
            const isOpen = forceOpen || open.has(t.table);
            return (
              <li key={t.table} role="treeitem" aria-expanded={isOpen} aria-selected={false}>
                <div className="group flex h-6 items-center gap-1 pr-2 pl-1 hover:bg-bg-muted">
                  <button
                    type="button"
                    onClick={() => toggle(t.table)}
                    className="flex size-4 shrink-0 items-center justify-center rounded-sm text-fg-subtle"
                    aria-label={isOpen ? `Collapse ${t.table}` : `Expand ${t.table}`}
                  >
                    <ChevronRight className={cn("size-3 transition-transform", isOpen && "rotate-90")} />
                  </button>
                  <button
                    type="button"
                    onClick={() => onInsert(t.table)}
                    title={`Insert ${t.table}${t.dataset_name && t.dataset_name !== t.table ? ` (${t.dataset_name})` : ""}`}
                    className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                  >
                    <Table2 className="size-3 shrink-0 text-fg-subtle" aria-hidden />
                    <span className="truncate font-mono font-medium">{t.table}</span>
                  </button>
                  {t.row_count !== null && t.row_count !== undefined ? (
                    <span className="shrink-0 text-2xs text-fg-faint tabular">{formatInt(t.row_count)}</span>
                  ) : null}
                </div>
                {isOpen ? (
                  <ul role="group">
                    {cols.map((c) => (
                      <li key={c.name} role="treeitem" aria-selected={false}>
                        <button
                          type="button"
                          onClick={() => onInsert(c.name)}
                          title={`Insert ${c.name} (${c.type})`}
                          className="flex h-5 w-full min-w-0 items-center gap-2 pr-2 pl-8 text-left hover:bg-bg-muted"
                        >
                          <span className="min-w-0 flex-1 truncate font-mono">{c.name}</span>
                          <span className="shrink-0 font-mono text-2xs text-fg-faint">{shortType(c.type)}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            );
          })
        )}
      </ul>
    </div>
  );
}
