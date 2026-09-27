"use client";
import * as React from "react";
import { ArrowRight, Database, Minus, Plus, Sigma, PenLine } from "lucide-react";
import type { InvestigationDiff } from "@/lib/api/types";
import { Badge } from "@/components/ui/badge";
import { SectionLabel } from "@/components/shell/page";
import { formatInt, formatPctChange, humanize, toNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface FieldChange {
  field: string;
  before: unknown;
  after: unknown;
}

const PCT_FIELDS = new Set(["pct_change", "share", "contribution_to_parent", "explanatory_power"]);

/** Normalizes node field changes from either `{field: [before, after]}` or before/after node snapshots. */
export function nodeFieldChanges(c: InvestigationDiff["node_changes"][number]): FieldChange[] {
  if (c.fields && !Array.isArray(c.fields)) {
    return Object.entries(c.fields).map(([field, [before, after]]) => ({ field, before, after }));
  }
  const names = Array.isArray(c.fields) ? c.fields : [];
  const b = (c.before ?? {}) as Record<string, unknown>;
  const a = (c.after ?? {}) as Record<string, unknown>;
  return names.map((field) => ({ field, before: b[field], after: a[field] }));
}

export function formatDiffValue(field: string, v: unknown): string {
  if (v === null || v === undefined) return "n/a";
  if (typeof v === "object" && v !== null && "share" in v) return formatDiffValue("share", (v as { share: unknown }).share);
  const n = toNumber(v);
  if (n !== null && PCT_FIELDS.has(field)) return formatPctChange(n);
  if (n !== null) return Math.abs(n) >= 1000 ? formatInt(Math.round(n)) : String(Math.round(n * 1000) / 1000);
  return String(v);
}

const CHANGE_META: Record<string, { icon: React.ComponentType<{ className?: string }>; tone: "positive" | "negative" | "warning" | "neutral"; label: string }> = {
  added: { icon: Plus, tone: "positive", label: "New" },
  removed: { icon: Minus, tone: "negative", label: "Gone" },
  changed: { icon: PenLine, tone: "warning", label: "Changed" },
  unchanged: { icon: Minus, tone: "neutral", label: "Same" },
};

/** Investigation diff (spec §61): what changed between runs, and which data or definitions changed underneath. */
export function DiffView({ diff, onSelectNode }: { diff: InvestigationDiff; onSelectNode?: (id: string) => void }) {
  const nodeChanges = diff.node_changes.filter((c) => c.change !== "unchanged");
  const nothing = !nodeChanges.length && !diff.dataset_version_changes.length && !diff.metric_version_changes.length;
  return (
    <div className="flex flex-col gap-4" data-testid="investigation-diff">
      {diff.summary?.length ? (
        <ul className="list-disc pl-4 text-sm">
          {diff.summary.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ul>
      ) : null}
      {nothing ? (
        <p className="text-sm text-fg-subtle">
          No differences: the rerun reproduced every node exactly{diff.unchanged_nodes ? ` (${diff.unchanged_nodes} nodes)` : ""}.
        </p>
      ) : null}
      {nodeChanges.length ? (
        <section>
          <SectionLabel className="mb-1.5">Findings that changed</SectionLabel>
          <ul className="flex flex-col divide-y divide-border rounded border border-border">
            {nodeChanges.map((c, i) => {
              const meta = CHANGE_META[c.change] ?? CHANGE_META.changed;
              const fields = nodeFieldChanges(c);
              const before = c.statement_before ?? (c.before?.statement as string | undefined) ?? null;
              const after = c.statement_after ?? (c.after?.statement as string | undefined) ?? c.statement ?? null;
              return (
                <li key={c.node_id ?? i} className="flex flex-col gap-1 px-3 py-2">
                  <div className="flex items-start gap-2">
                    <Badge tone={meta.tone} className="mt-0.5">
                      <meta.icon /> {meta.label}
                    </Badge>
                    <div className="min-w-0 flex-1 text-sm">
                      {before && after && before !== after ? (
                        <>
                          <p className="text-fg-subtle line-through decoration-fg-faint">{before}</p>
                          <p>{after}</p>
                        </>
                      ) : (
                        <p className={cn(c.change === "removed" && "text-fg-subtle line-through")}>{after ?? before}</p>
                      )}
                    </div>
                    {c.node_id && onSelectNode && c.change !== "removed" ? (
                      <button type="button" onClick={() => onSelectNode(c.node_id!)} className="shrink-0 text-xs text-accent hover:underline">
                        Show
                      </button>
                    ) : null}
                  </div>
                  {fields.length ? (
                    <dl className="ml-16 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-xs">
                      {fields.map((f) => (
                        <React.Fragment key={f.field}>
                          <dt className="text-fg-subtle">{humanize(f.field)}</dt>
                          <dd className="flex items-center gap-1.5 tabular">
                            <span className="text-fg-subtle">{formatDiffValue(f.field, f.before)}</span>
                            <ArrowRight className="size-3 text-fg-faint" aria-label="changed to" />
                            <span className="font-medium">{formatDiffValue(f.field, f.after)}</span>
                          </dd>
                        </React.Fragment>
                      ))}
                    </dl>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
      {diff.dataset_version_changes.length ? (
        <section>
          <SectionLabel className="mb-1.5">Underlying data that changed</SectionLabel>
          <ul className="flex flex-col gap-1">
            {diff.dataset_version_changes.map((d, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2 text-sm">
                <Database className="size-3.5 text-fg-subtle" />
                <span className="font-mono text-xs">{d.table ?? d.dataset ?? d.after?.table ?? d.before?.table}</span>
                <span className="text-xs text-fg-subtle tabular">
                  {d.before ? `${formatInt(d.before.row_count ?? null)} rows · ${String(d.before.content_hash ?? "").slice(0, 8)}` : "not present"}
                </span>
                <ArrowRight className="size-3 text-fg-faint" />
                <span className="text-xs tabular">
                  {d.after ? `${formatInt(d.after.row_count ?? null)} rows · ${String(d.after.content_hash ?? "").slice(0, 8)}` : "removed"}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {diff.metric_version_changes.length ? (
        <section>
          <SectionLabel className="mb-1.5">Metric definitions that changed</SectionLabel>
          <ul className="flex flex-col gap-1">
            {diff.metric_version_changes.map((m, i) => (
              <li key={i} className="flex items-center gap-2 text-sm">
                <Sigma className="size-3.5 text-fg-subtle" />
                <span>{humanize(m.metric ?? m.metric_id)}</span>
                <code className="text-xs text-fg-subtle">{String(m.before ?? "n/a").slice(0, 12)}</code>
                <ArrowRight className="size-3 text-fg-faint" />
                <code className="text-xs">{String(m.after ?? "n/a").slice(0, 12)}</code>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
