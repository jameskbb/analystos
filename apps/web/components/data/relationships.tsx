"use client";
import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, ArrowRight, Check, ChevronDown, ChevronRight, RefreshCw, Trash2, X } from "lucide-react";
import { relationships, type Cardinality } from "@/lib/api/resources/data";
import type { Relationship } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { NativeSelect } from "@/components/ui/input";
import { StatusBadge } from "@/components/analysis/status";
import { CodeBlock } from "@/components/code/code-block";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export const CARDINALITY_LABEL: Record<string, string> = {
  one_to_one: "1 : 1",
  one_to_many: "1 : N",
  many_to_one: "N : 1",
  many_to_many: "N : M",
};

const CONF_TONE: Record<string, "positive" | "accent" | "warning"> = { high: "positive", medium: "accent", low: "warning" };

export interface JoinAnalysisView {
  observed_cardinality?: string;
  fanout_factor?: number | null;
  orphan_pct?: number | null;
  orphan_count?: number | null;
  warnings: string[];
  sql?: string | null;
}

/** Normalizes the engine's JoinAnalysis dict (field names may vary slightly). */
export function readJoinAnalysis(ja: Record<string, unknown> | null | undefined): JoinAnalysisView | null {
  if (!ja) return null;
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const warnings = Array.isArray(ja.warnings) ? (ja.warnings as unknown[]).map(String) : [];
  return {
    observed_cardinality: typeof ja.observed_cardinality === "string" ? ja.observed_cardinality : undefined,
    fanout_factor: num(ja.fanout_factor),
    orphan_pct: num(ja.orphan_pct),
    orphan_count: num(ja.orphan_count),
    warnings,
    sql: typeof ja.sql === "string" ? ja.sql : null,
  };
}

export function isManyToMany(r: Relationship): boolean {
  const ja = readJoinAnalysis(r.join_analysis as Record<string, unknown> | null);
  return r.cardinality === "many_to_many" || ja?.observed_cardinality === "many_to_many";
}

function signalText(s: unknown): string {
  if (typeof s === "string") return s;
  if (s && typeof s === "object") {
    const o = s as Record<string, unknown>;
    if (typeof o.description === "string") return o.description;
    if (typeof o.name === "string") return o.value !== undefined ? `${o.name}: ${String(o.value)}` : o.name;
    return JSON.stringify(s);
  }
  return String(s);
}

function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "n/a";
  const n = v > 1 ? v : v * 100;
  return `${n.toFixed(n < 1 && n > 0 ? 2 : 1)}%`;
}

/** One relationship with evidence, join analysis and approve/reject controls. */
export function RelationshipRow({ rel }: { rel: Relationship }) {
  const { id: ws, canEdit } = useWorkspace();
  const qc = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [card, setCard] = React.useState<Cardinality>((rel.cardinality as Cardinality) ?? "many_to_one");
  const invalidate = () => void qc.invalidateQueries({ queryKey: ["ws", ws, "relationships"] });
  const approve = useMutation({
    mutationFn: () => relationships.approve(ws, rel.id, card !== rel.cardinality ? { cardinality: card } : {}),
    onSuccess: () => {
      invalidate();
      toast.success(`Approved ${rel.from_table}.${rel.from_col} → ${rel.to_table}.${rel.to_col}`);
    },
    onError: (e: Error) => toast.error("Approve failed", { description: e.message }),
  });
  const reject = useMutation({
    mutationFn: () => relationships.reject(ws, rel.id),
    onSuccess: invalidate,
    onError: (e: Error) => toast.error("Reject failed", { description: e.message }),
  });
  const analyze = useMutation({
    mutationFn: () => relationships.analyze(ws, rel.id),
    onSuccess: () => {
      invalidate();
      setOpen(true);
    },
    onError: (e: Error) => toast.error("Join analysis failed", { description: e.message }),
  });
  const remove = useMutation({
    mutationFn: () => relationships.remove(ws, rel.id),
    onSuccess: invalidate,
    onError: (e: Error) => toast.error("Delete failed", { description: e.message }),
  });
  const ja = readJoinAnalysis(rel.join_analysis as Record<string, unknown> | null);
  const m2m = isManyToMany(rel);
  const fanout = ja?.fanout_factor != null && ja.fanout_factor > 1.0001;
  const busy = approve.isPending || reject.isPending || analyze.isPending || remove.isPending;

  return (
    <li className={cn("border-b border-border last:border-b-0", m2m && "bg-negative-soft/40")} data-testid="relationship-row">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-label={open ? "Hide details" : "Show details"}
          className="rounded p-0.5 text-fg-subtle hover:bg-bg-muted"
        >
          {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        </button>
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5 font-mono text-xs">
          <span className="truncate">
            <span className="text-fg-subtle">{rel.from_table}.</span>
            <span className="font-medium">{rel.from_col}</span>
          </span>
          <ArrowRight className="size-3 shrink-0 text-fg-faint" aria-label="references" />
          <span className="truncate">
            <span className="text-fg-subtle">{rel.to_table}.</span>
            <span className="font-medium">{rel.to_col}</span>
          </span>
        </div>
        <Badge tone={m2m ? "negative" : "outline"} className="font-mono">
          {CARDINALITY_LABEL[rel.cardinality] ?? rel.cardinality}
        </Badge>
        {m2m ? (
          <Badge tone="negative">
            <AlertTriangle /> Many-to-many
          </Badge>
        ) : null}
        {fanout && !m2m ? <Badge tone="warning">Fan-out ×{ja!.fanout_factor!.toFixed(2)}</Badge> : null}
        <Badge tone={CONF_TONE[rel.confidence] ?? "neutral"}>{rel.confidence} confidence</Badge>
        {rel.overlap_pct != null ? (
          <span className="text-xs text-fg-subtle tabular" title="Share of from-values found in the referenced column">
            {pct(rel.overlap_pct)} overlap
          </span>
        ) : null}
        <StatusBadge status={rel.status} />
        {canEdit ? (
          <div className="flex items-center gap-1">
            {rel.status !== "approved" ? (
              <>
                <NativeSelect
                  aria-label="Cardinality to approve"
                  value={card}
                  onChange={(e) => setCard(e.target.value as Cardinality)}
                  className="h-6 w-20 text-xs"
                >
                  {Object.entries(CARDINALITY_LABEL).map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </NativeSelect>
                <Button size="xs" variant="primary" onClick={() => approve.mutate()} disabled={busy}>
                  <Check /> Approve
                </Button>
              </>
            ) : null}
            {rel.status !== "rejected" ? (
              <Button size="xs" variant={rel.status === "approved" ? "secondary" : "ghost"} onClick={() => reject.mutate()} disabled={busy}>
                <X /> {rel.status === "approved" ? "Revoke" : "Reject"}
              </Button>
            ) : null}
            <Tooltip content="Re-run join analysis">
              <Button size="icon-xs" variant="ghost" onClick={() => analyze.mutate()} disabled={busy} aria-label="Analyze join">
                <RefreshCw className={cn(analyze.isPending && "animate-spin")} />
              </Button>
            </Tooltip>
            {rel.origin === "manual" ? (
              <Tooltip content="Delete">
                <Button size="icon-xs" variant="ghost" onClick={() => remove.mutate()} disabled={busy} aria-label="Delete relationship">
                  <Trash2 />
                </Button>
              </Tooltip>
            ) : null}
          </div>
        ) : null}
      </div>
      {open ? (
        <div className="grid gap-3 border-t border-border bg-bg-subtle px-3 py-2.5 text-sm md:grid-cols-2">
          <div>
            <div className="mb-1 text-2xs font-semibold tracking-wider text-fg-subtle uppercase">Why it was suggested</div>
            {rel.signals?.length ? (
              <ul className="flex flex-col gap-0.5 text-xs">
                {rel.signals.map((s, i) => (
                  <li key={i} className="flex gap-1.5">
                    <span className="mt-1.5 size-1 shrink-0 rounded-full bg-fg-subtle" aria-hidden />
                    {signalText(s)}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-fg-subtle">{rel.origin === "manual" ? "Declared manually." : "No signals recorded."}</p>
            )}
          </div>
          <div>
            <div className="mb-1 text-2xs font-semibold tracking-wider text-fg-subtle uppercase">Join analysis</div>
            {ja ? (
              <div className="flex flex-col gap-1.5 text-xs">
                <dl className="grid grid-cols-3 gap-2">
                  <div>
                    <dt className="text-fg-subtle">Observed</dt>
                    <dd className={cn("font-mono font-medium", ja.observed_cardinality === "many_to_many" && "text-negative")}>
                      {ja.observed_cardinality ? CARDINALITY_LABEL[ja.observed_cardinality] ?? ja.observed_cardinality : "n/a"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-fg-subtle">Fan-out</dt>
                    <dd className={cn("font-medium tabular", fanout && "text-warning")}>
                      {ja.fanout_factor != null ? `×${ja.fanout_factor.toFixed(2)}` : "n/a"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-fg-subtle">Orphans</dt>
                    <dd className="font-medium tabular">
                      {pct(ja.orphan_pct)}
                      {ja.orphan_count != null ? ` (${ja.orphan_count})` : ""}
                    </dd>
                  </div>
                </dl>
                {ja.warnings.map((w, i) => (
                  <p key={i} className="flex gap-1.5 text-warning">
                    <AlertTriangle className="mt-0.5 size-3.5 shrink-0" /> {w}
                  </p>
                ))}
                {ja.sql ? <CodeBlock code={ja.sql} maxHeight={140} /> : null}
              </div>
            ) : (
              <p className="text-xs text-fg-subtle">
                Not analyzed yet.{" "}
                {canEdit ? (
                  <button type="button" className="text-accent hover:underline" onClick={() => analyze.mutate()}>
                    Run join analysis
                  </button>
                ) : null}
              </p>
            )}
            {m2m ? (
              <p className="mt-2 rounded border border-negative/30 bg-bg px-2 py-1.5 text-xs text-negative">
                Many-to-many joins multiply rows. The semantic compiler pre-aggregates to each metric&apos;s grain or refuses
                the query rather than double count.
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
    </li>
  );
}

export function RelationshipList({ items, empty }: { items: Relationship[]; empty?: React.ReactNode }) {
  if (!items.length) return <>{empty ?? null}</>;
  return <ul aria-label="Relationships">{items.map((r) => <RelationshipRow key={r.id} rel={r} />)}</ul>;
}
