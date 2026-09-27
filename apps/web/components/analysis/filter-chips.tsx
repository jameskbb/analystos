"use client";
import * as React from "react";
import { Filter, Calendar, X, Layers, GitCompare } from "lucide-react";
import type { FilterContextItem, FilterSpec, TimeWindow } from "@/lib/api/types";
import { formatWindow, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";

type RawFilter =
  | FilterContextItem
  | FilterSpec
  | { dimension: string; op?: string; values?: unknown[]; label?: string | null }
  | null
  | undefined;

const OP_LABEL: Record<string, string> = {
  eq: "=",
  "=": "=",
  neq: "≠",
  "!=": "≠",
  in: "in",
  not_in: "not in",
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  between: "between",
  contains: "contains",
  is_null: "is empty",
  not_null: "is not empty",
};

function valuesText(values: unknown[] | undefined, op?: string): string {
  const vs = (values ?? []).map((v) => (v === null ? "∅" : String(v)));
  if (op === "between" && vs.length === 2) return `${vs[0]} – ${vs[1]}`;
  if (op === "is_null" || op === "not_null") return "";
  if (vs.length > 3) return `${vs.slice(0, 3).join(", ")} +${vs.length - 3}`;
  return vs.join(", ");
}

/** Converts any filter shape the API returns into display chips. */
export function normalizeFilterContext(items: RawFilter[] | null | undefined): FilterContextItem[] {
  if (!items) return [];
  const out: FilterContextItem[] = [];
  for (const it of items) {
    if (!it) continue;
    if ("value" in it && typeof (it as FilterContextItem).value === "string" && "label" in it && it.label) {
      const c = it as FilterContextItem;
      out.push(c.op === "ignored" && !c.ignored ? { ...c, ignored: true } : c);
      continue;
    }
    const f = it as { dimension: string; op?: string; values?: unknown[]; label?: string | null };
    if (!f.dimension) continue;
    const op = f.op ?? "eq";
    const opText = op === "eq" || op === "in" ? "" : `${OP_LABEL[op] ?? op} `;
    out.push({
      label: f.label || humanize(f.dimension),
      value: `${opText}${valuesText(f.values, op)}`.trim() || (OP_LABEL[op] ?? op),
      kind: "dimension",
    });
  }
  return out;
}

export function timeWindowChip(w: TimeWindow | null | undefined, label = "Date"): FilterContextItem | null {
  if (!w) return null;
  return { label: w.label ? `${label}` : label, value: w.label ? `${w.label} (${formatWindow(w.start, w.end)})` : formatWindow(w.start, w.end), kind: "time" };
}

const KIND_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  time: Calendar,
  baseline: GitCompare,
  segment: Layers,
};

/**
 * The filter context row shown on every artifact (spec §47). When no filters apply it says so
 * explicitly, so an absent chip can never hide a filter.
 */
export function FilterChips({
  items,
  onRemove,
  className,
  emptyLabel = "No filters: all data",
  size = "sm",
}: {
  items: RawFilter[] | null | undefined;
  onRemove?: (index: number) => void;
  className?: string;
  emptyLabel?: string;
  size?: "sm" | "xs";
}) {
  const chips = normalizeFilterContext(items);
  return (
    <div
      className={cn("flex flex-wrap items-center gap-1", className)}
      role="list"
      aria-label="Filter context"
      data-testid="filter-chips"
    >
      {chips.length === 0 ? (
        <span
          role="listitem"
          className={cn(
            "inline-flex items-center gap-1 rounded-sm border border-dashed border-border-strong px-1.5 text-fg-subtle",
            size === "sm" ? "h-5 text-xs" : "h-[18px] text-2xs",
          )}
        >
          <Filter className="size-3" aria-hidden />
          {emptyLabel}
        </span>
      ) : (
        chips.map((c, i) => {
          const Icon = KIND_ICON[c.kind ?? ""] ?? Filter;
          return (
            <span
              key={`${c.label}-${i}`}
              role="listitem"
              className={cn(
                "inline-flex max-w-full items-center gap-1 rounded-sm border border-border bg-bg-subtle px-1.5",
                size === "sm" ? "h-5 text-xs" : "h-[18px] text-2xs",
                c.inherited && "border-dashed",
                c.ignored && "border-dashed opacity-70 [&>span]:line-through",
              )}
              title={
                c.ignored
                  ? "Not applied: this view cannot use this filter"
                  : c.inherited
                    ? "Inherited from the parent context"
                    : undefined
              }
              data-ignored={c.ignored ? "true" : undefined}
            >
              <Icon className="size-3 shrink-0 text-fg-subtle" aria-hidden />
              <span className="text-fg-subtle">{c.label}</span>
              <span className="truncate font-medium text-fg tabular">{c.value}</span>
              {onRemove ? (
                <button
                  type="button"
                  onClick={() => onRemove(i)}
                  className="-mr-0.5 rounded-sm p-0.5 text-fg-subtle hover:bg-bg-muted hover:text-fg"
                  aria-label={`Remove filter ${c.label} ${c.value}`}
                >
                  <X className="size-3" />
                </button>
              ) : null}
            </span>
          );
        })
      )}
    </div>
  );
}
