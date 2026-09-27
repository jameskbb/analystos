"use client";
import * as React from "react";
import { CheckCircle2, Circle, EyeOff } from "lucide-react";
import type { ReportBlockSpec, ReportOut } from "@/lib/api/resources/outputs";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";
import { BLOCK_LABELS, reviewState } from "./model";

function blockSummary(b: ReportBlockSpec): string {
  const t =
    b.text ||
    b.title ||
    b.label ||
    b.snapshot?.statement ||
    b.markdown?.split("\n").find((l) => l.trim())?.replace(/^#+\s*/, "") ||
    "";
  return t.length > 80 ? `${t.slice(0, 80)}…` : t;
}

/**
 * Review checklist (spec §64): the analyst marks each block reviewed or excludes it; publishing is
 * gated until every included block is reviewed.
 */
export function ReviewPanel({
  report,
  blocks,
  readOnly,
  onToggle,
  onFocusBlock,
  refused = [],
}: {
  /** Block ids the server refused to publish because they are not reviewed (409 unreviewed_blocks). */
  refused?: string[];
  report: Pick<ReportOut, "status">;
  blocks: ReportBlockSpec[];
  readOnly: boolean;
  onToggle: (id: string, patch: { reviewed?: boolean; excluded?: boolean }) => void;
  onFocusBlock: (id: string) => void;
}) {
  const state = reviewState({ status: report.status, blocks });
  const pct = state.included ? Math.round((state.reviewed / state.included) * 100) : 0;
  return (
    <aside aria-label="Review checklist" className="flex flex-col gap-2 rounded-md border border-border bg-bg p-3 no-print">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">Review</h2>
        <span className="text-xs text-fg-subtle tabular">
          {state.reviewed} / {state.included} reviewed
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-bg-muted" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Review progress">
        <div className="h-full rounded-full bg-positive transition-[width]" style={{ width: `${pct}%` }} />
      </div>
      {state.blockers.length && !state.readOnly ? (
        <ul className="flex flex-col gap-0.5 text-xs text-fg-subtle">
          {state.blockers.map((b) => (
            <li key={b}>· {b}</li>
          ))}
        </ul>
      ) : null}
      <ul className="-mx-1 flex max-h-[60vh] flex-col overflow-auto scrollbar-thin">
        {blocks.map((b) => (
          <li
            key={b.id}
            className={cn(
              "flex items-start gap-2 rounded px-1 py-1 hover:bg-bg-subtle",
              b.excluded && "opacity-60",
              refused.includes(b.id) && !b.reviewed && !b.excluded && "bg-negative-soft ring-1 ring-negative/40",
            )}
            data-refused={refused.includes(b.id) && !b.reviewed && !b.excluded ? "true" : undefined}
          >
            <Checkbox
              className="mt-0.5"
              checked={!!b.reviewed}
              disabled={readOnly || b.excluded}
              onCheckedChange={(c) => onToggle(b.id, { reviewed: c === true })}
              aria-label={`Mark ${BLOCK_LABELS[b.type] ?? b.type} block reviewed`}
            />
            <button type="button" onClick={() => onFocusBlock(b.id)} className="min-w-0 flex-1 text-left">
              <div className="flex items-center gap-1 text-2xs tracking-wide text-fg-subtle uppercase">
                {b.excluded ? <EyeOff className="size-3" /> : b.reviewed ? <CheckCircle2 className="size-3 text-positive" /> : <Circle className="size-3" />}
                {BLOCK_LABELS[b.type] ?? b.type}
                {b.excluded ? " · excluded" : ""}
              </div>
              <div className="truncate text-xs">{blockSummary(b) || <span className="text-fg-faint italic">No text</span>}</div>
            </button>
            {!readOnly ? (
              <button
                type="button"
                onClick={() => onToggle(b.id, { excluded: !b.excluded, reviewed: false })}
                className="shrink-0 rounded px-1 text-2xs text-fg-subtle hover:bg-bg-muted hover:text-fg"
                aria-label={b.excluded ? "Include block in the report" : "Exclude block from the report"}
              >
                {b.excluded ? "Include" : "Exclude"}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </aside>
  );
}
