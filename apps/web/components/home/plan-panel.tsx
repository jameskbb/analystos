"use client";
import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { ArrowRight, GitBranch } from "lucide-react";
import { investigations } from "@/lib/api/endpoints";
import type { PlanVariance } from "@/lib/api/types";
import { formatPctChange, formatSignedChange, formatValue } from "@/lib/format";
import { useWorkspace } from "@/components/providers/workspace";
import { Panel } from "@/components/shell/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import { planContradiction, planQuestion, planSentence, planStatusLabel, planTone } from "./plan";

function PlanTile({ v }: { v: PlanVariance }) {
  const { href } = useWorkspace();
  const tone = planTone(v);
  return (
    <Link
      href={href(`/metrics/${v.metric_id}`)}
      className="flex min-w-0 flex-col gap-1 border-r border-b border-border p-3 last:border-r-0 hover:bg-bg-subtle"
      data-testid="plan-tile"
      data-status={v.status}
      title={`Actual and ${v.plan_label} over ${v.period}, each from an executed query`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs text-fg-subtle">{v.label}</span>
        <Badge tone={tone === "negative" ? "negative" : tone === "positive" ? "positive" : "neutral"}>{planStatusLabel(v)}</Badge>
      </div>
      <div className="text-lg font-semibold tracking-tight tabular">{formatValue(v.actual, v.format)}</div>
      <div className="flex flex-wrap items-baseline gap-x-1.5 text-xs tabular">
        <span className={cn("font-medium", tone === "negative" && "text-negative", tone === "positive" && "text-positive")}>
          {formatPctChange(v.variance_pct)} ({formatSignedChange(v.variance, v.format)})
        </span>
        <span className="text-fg-subtle">vs plan {formatValue(v.plan, v.format, { compact: true })}</span>
      </div>
    </Link>
  );
}

/**
 * Home "Against plan": the latest closed quarter's headline metrics against their plan metrics,
 * and, when one landed on plan while another missed, the contradiction with an "Investigate why"
 * that starts a real investigation of it (interpreted, planned and executed like any question).
 */
export function PlanPanel({ items, note }: { items: PlanVariance[]; note?: string | null }) {
  const { id: ws, href } = useWorkspace();
  const router = useRouter();
  const c = planContradiction(items);
  const ask = useMutation({
    mutationFn: (question: string) => investigations.create(ws, { question }),
    onSuccess: (inv) => router.push(href(`/investigate/${inv.id}`)),
  });
  const period = items[0]?.period;
  return (
    <Panel
      title={period ? `Against plan · ${period}` : "Against plan"}
      description={note ?? undefined}
      actions={
        <Button asChild variant="ghost" size="xs">
          <Link href={href("/metrics")}>
            All metrics <ArrowRight />
          </Link>
        </Button>
      }
    >
      {c ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-bg-subtle px-3 py-2.5" role="note" aria-label="Plan contradiction">
          <p className="min-w-0 flex-1 text-sm">{planSentence(c)}</p>
          <Button variant="primary" size="sm" disabled={ask.isPending} onClick={() => ask.mutate(planQuestion(c))} title={planQuestion(c)}>
            {ask.isPending ? <Spinner className="text-accent-fg" /> : <GitBranch />} Investigate why
          </Button>
        </div>
      ) : null}
      <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6">
        {items.map((v) => (
          <PlanTile key={v.metric_id} v={v} />
        ))}
      </div>
      {ask.error ? <p className="border-t border-border px-3 py-1.5 text-xs text-negative">{ask.error.message}</p> : null}
    </Panel>
  );
}
